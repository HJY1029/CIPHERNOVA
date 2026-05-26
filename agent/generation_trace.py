"""可选：在 stderr 逐步打印「已组装 Prompt → 正在请求 LLM → 已返回」便于实验与排错。"""
from __future__ import annotations

import hashlib
import os
import sys
from typing import Any, Dict, Optional


def tracing_on(kwargs: Optional[Dict[str, Any]] = None) -> bool:
    if kwargs and kwargs.get("_trace_generation"):
        return True
    v = (os.environ.get("AICRYPTO_TRACE_GENERATION") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _prompt_full_dump() -> bool:
    v = (os.environ.get("AICRYPTO_TRACE_PROMPT_FULL") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def model_label(agent) -> str:
    try:
        return str(agent.config.get_llm_config(agent.provider).get("model", "?"))
    except Exception:
        return "?"


def compact_label(agent, kwargs: Dict[str, Any]) -> bool:
    return agent.provider.lower() in ("openai", "claude", "codex")


def emit_prompt_ready(
    *,
    step: str,
    agent,
    algorithm: str,
    mode: Optional[str],
    language: str,
    kwargs: Dict[str, Any],
    user_prompt: str,
    system_prompt: Optional[str],
) -> None:
    if not tracing_on(kwargs):
        return
    pa = kwargs.get("prompt_ablation")
    pa_s = repr(pa) if pa is not None else "(未设置；加载器按默认处理)"
    h = hashlib.sha256(user_prompt.encode("utf-8", errors="replace")).hexdigest()[:12]
    n = len(user_prompt)
    lines = user_prompt.count("\n") + (1 if user_prompt else 0)
    sy = system_prompt or ""
    bar = "=" * 72
    _log("")
    _log(bar)
    _log(f'[aicrypto-trace] 步骤「{step}」— Prompt 已就绪（build_prompt → PromptLoader.get_prompt）')
    _log(bar)
    _log(f"  provider={agent.provider}  model={model_label(agent)}")
    _log(f"  algorithm={algorithm}  mode={mode!r}  language={language}")
    _log(f"  prompt_ablation={pa_s}  compact={compact_label(agent, kwargs)}")
    _log(f"  用户提示: {n} 字符, {lines} 行, sha256[:12]={h}")
    _log(f"  系统提示: {len(sy)} 字符")
    if _prompt_full_dump():
        _log("  ----- 用户提示全文（AICRYPTO_TRACE_PROMPT_FULL=1）-----")
        _log(user_prompt)
        _log("  ----- 系统提示全文 -----")
        _log(sy)
        _log("  ----- 全文结束 -----")
    else:
        cap = 2400
        if n <= cap:
            _log("  ----- 用户提示（全文）-----")
            _log(user_prompt)
        else:
            _log(
                f"  ----- 用户提示节选（前 {cap} 字符；完整请设置环境变量 AICRYPTO_TRACE_PROMPT_FULL=1）-----"
            )
            _log(user_prompt[:cap] + "\n  …")
    _log("")


def emit_llm_begin(
    *,
    step: str,
    agent,
    kwargs: Dict[str, Any],
    user_chars: int,
    system_chars: int,
) -> None:
    if not tracing_on(kwargs):
        return
    bar = "-" * 72
    _log(bar)
    _log(
        f'[aicrypto-trace] 步骤「{step}」— 调用 LLM.generate（{agent.provider} / {model_label(agent)}）'
    )
    _log(f"  约 {user_chars} 字符（用户）+ {system_chars} 字符（system）")
    _log(bar)


def emit_llm_end(
    *,
    step: str,
    kwargs: Dict[str, Any],
    seconds: float,
    reply_chars: int,
) -> None:
    if not tracing_on(kwargs):
        return
    _log(
        f'[aicrypto-trace] 步骤「{step}」— LLM 已返回, 耗时 {seconds:.2f}s, 原始回复 {reply_chars} 字符\n'
    )
