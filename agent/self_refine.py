"""
Self-Refine：迭代式自我反馈与修正（Madaan et al.）

思路与 [self-refine](https://github.com/madaan/self-refine) 一致：先让模型对当前产出给出批判性反馈（Feedback），
再基于反馈生成改进版本（Refine）。本项目中与「标准测试 / 编译错误」驱动的 improve 流程互补：
- critic_before_test：在跑 harness 之前增加一轮「自我审稿」；
- max_refine_rounds：测试失败时，在单次「大轮」重试内可连续多轮 improve，无需每次从头 generate。

论文：Self-Refine: Iterative Refinement with Self-Feedback (arXiv:2303.17651)
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from utils.logger import setup_logger
from agent.prompt_builder import get_system_prompt
from agent.code_processing import extract_code

logger = setup_logger()

CRITIC_SYSTEM = """你是一位严谨的密码学实现审查员。你的任务是对给定代码进行批判性分析，指出可能导致
标准测试向量不匹配、算法错误或 API 误用的问题。只输出有条理的审查要点（Markdown 列表），不要重写完整代码。"""


def format_test_data_critic_summary(test_data: Optional[Dict[str, Any]], algorithm: str, mode: Optional[str]) -> str:
    """为 Critic 提供简短的标准测试上下文（不含过长 hex 全文）。"""
    if not test_data:
        return f"算法: {algorithm}，模式: {mode or 'N/A'}。当前无加载到标准测试数据摘要。"
    lines = [f"算法: {algorithm}", f"模式: {mode or 'N/A'}"]
    pt = test_data.get("plaintext")
    if pt:
        lines.append(f"标准明文(hex)长度: {len(pt)} 字符，前缀: {pt[:48]}...")
    key = test_data.get("key")
    if key:
        lines.append(f"标准密钥(hex)长度: {len(key)} 字符，前缀: {key[:32]}...")
    iv = test_data.get("iv")
    if iv:
        lines.append(f"标准 IV(hex)长度: {len(iv)} 字符，前缀: {iv[:32]}...")
    exp = test_data.get("expected_ciphertext")
    if exp:
        lines.append(f"期望密文(hex)长度: {len(exp)} 字符，前缀: {exp[:48]}...")
    return "\n".join(lines)


async def run_critic_feedback(
    agent,
    code: str,
    algorithm: str,
    mode: Optional[str],
    language: str,
    operation: str,
    test_data_summary: str,
) -> str:
    """Feedback 阶段：模型对当前代码给出审查意见。"""
    lang = language.lower()
    user = f"""## 任务上下文
{test_data_summary}

## 目标
实现 {algorithm} {mode or ""} 的 {operation} 相关逻辑（语言: {language}），且须与上述标准测试向量一致。

## 待审查代码
```{lang}
{code}
```

请列出：
1. 与算法/模式不符或可能算错的地方（含填充、IV/nonce、端序、块链）
2. 与标准 hex 输入输出格式不一致的风险（大小写、空格、是否输出多余前缀）
3. 编译或链接层面可能的问题（若可见）
4. 其他高风险点

只输出审查列表，不要输出完整新代码。"""
    raw = await agent.llm.generate(user, CRITIC_SYSTEM)
    critique = (raw or "").strip()
    logger.info(f"Self-Refine Critic 输出长度: {len(critique)} 字符")
    return critique


REFINE_FROM_CRITIQUE_USER = """你是一位资深密码学代码工程师。下面是一份「自我审查」反馈，请**仅在必要时**
修改代码以消除这些问题，并保证程序仍可从环境变量读取 TEST_PLAINTEXT / TEST_KEY / TEST_IV（及项目约定的变量名），
输出与标准测试一致的密文 hex。

## 审查反馈
{critique}

## 当前代码
```{lang}
{code}
```

请输出**完整可编译/可运行**的修订后代码（仅代码或必要的最短说明，优先完整源码）。"""


async def run_refine_from_critique(
    agent,
    code: str,
    critique: str,
    algorithm: str,
    mode: Optional[str],
    language: str,
    operation: str,
) -> Tuple[str, float]:
    """Refine 阶段：根据 Critic 反馈修订代码（Self-Refine 中的一轮 Iterate）。"""
    lang = language.lower()
    prompt = REFINE_FROM_CRITIQUE_USER.format(critique=critique, lang=lang, code=code)
    system = get_system_prompt(language)
    t0 = time.time()
    raw = await agent.llm.generate(prompt, system)
    elapsed = time.time() - t0
    new_code = extract_code(raw, language)
    if not new_code or len(new_code.strip()) < 20:
        logger.warning("Self-Refine Refine 未得到有效代码，保留原版")
        return code, elapsed
    return new_code, elapsed
