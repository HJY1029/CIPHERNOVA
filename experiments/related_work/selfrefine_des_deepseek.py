#!/usr/bin/env python3
"""
Self-Refine 风格（初始化 → 自反馈 → 迭代改进）+ DeepSeek，用于 DES 12 格对比实验。

- **不**使用本仓库 ``PromptLoader`` / ``build_prompt`` / ``CryptoAgent``：
  任务说明仅由 ``rw_des_tasks.jsonl`` 的字段在脚本内拼成短英文规范（中性 I/O 约定）。
- **不**复用 ``external/self-refine`` 的 ``prompt_lib``（旧版 OpenAI 接口），
  改为 ``openai`` SDK 的兼容端点，便于对接 DeepSeek。

环境变量（任选其一，见 ``--api-key-env``）：
  ``DEEPSEEK_API_KEY`` 或 ``OPENAI_API_KEY``

示例：

  set DEEPSEEK_API_KEY=sk-...
  python experiments/related_work/selfrefine_des_deepseek.py \\
    --out-dir experiments/selfrefine_deepseek_out \\
    --max-iterations 3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _task_spec(row: Dict[str, Any]) -> str:
    """由结构化字段组成的极简规范（非 prompts/ 模板库）。"""
    lang = (row.get("language") or "python").lower()
    mode = (row.get("mode") or "ECB").upper()
    algo = row.get("algorithm") or "DES"
    need_iv = mode != "ECB"
    env_iv = (
        "Environment variables (hex strings): TEST_PLAINTEXT, TEST_KEY, TEST_IV."
        if need_iv
        else "Environment variables (hex strings): TEST_PLAINTEXT, TEST_KEY. "
        "Ignore IV for ECB."
    )
    out_rule = "Print only the ciphertext as lowercase hexadecimal on stdout (single line or trimmed)."
    return (
        f"Implement {algo} encryption in {mode} mode as a complete single-file program in {lang}. "
        f"{env_iv} {out_rule} "
        "Do not hard-code the test vectors in the docstring as the final answer; read from env at runtime."
    )


def _fence_lang(lang: str) -> str:
    if lang == "cpp":
        return "cpp"
    return lang


def _extract_code(text: str, language: str) -> str:
    text = text.strip()
    fence = _fence_lang(language.lower())
    patterns = [
        rf"```{fence}\s*(.*?)```",
        r"```(?:python|c|cpp)\s*(.*?)```",
        r"```\s*(.*?)```",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return text


def _chat(
    client: Any,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    ch = resp.choices[0].message
    return (ch.content or "").strip()


def _self_refine_loop(
    client: Any,
    model: str,
    row: Dict[str, Any],
    spec: str,
    *,
    max_iterations: int,
    temperature: float,
    max_tokens_init: int,
    max_tokens_fb: int,
    sleep_s: float,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Self-Refine：首版代码 → 自评 → 按反馈修订（可多轮）。"""
    lang = (row.get("language") or "python").lower()
    trace: List[Dict[str, Any]] = []

    messages_init = [
        {
            "role": "system",
            "content": "You write concise, runnable cryptography utility programs. "
            "Follow the I/O contract exactly. Prefer standard libraries appropriate to the language.",
        },
        {
            "role": "user",
            "content": spec + "\n\nOutput only the full source code, optionally wrapped in one fenced code block.",
        },
    ]
    raw0 = _chat(client, model, messages_init, temperature, max_tokens_init)
    code = _extract_code(raw0, lang)
    trace.append({"step": "init", "raw": raw0, "code": code})

    if max_iterations <= 1:
        return code, trace

    for it in range(1, max_iterations):
        if sleep_s > 0:
            time.sleep(sleep_s)
        fb_messages = [
            {
                "role": "system",
                "content": "You review code for cryptographic API correctness and the stated environment-variable I/O.",
            },
            {
                "role": "user",
                "content": (
                    "Program:\n```\n"
                    + code
                    + "\n```\n\n"
                    "List specific issues (wrong mode, padding, endianness, env var names, output format). "
                    "If it likely satisfies the task, say: OK_TO_SHIP. Keep under 30 lines."
                ),
            },
        ]
        feedback = _chat(client, model, fb_messages, min(0.3, temperature), max_tokens_fb)
        trace.append({"step": f"feedback_{it}", "text": feedback})
        if "OK_TO_SHIP" in feedback.upper():
            break
        if sleep_s > 0:
            time.sleep(sleep_s)
        rev_messages = [
            {
                "role": "system",
                "content": "You revise programs according to review notes. Output only full revised source code.",
            },
            {
                "role": "user",
                "content": (
                    "Task specification:\n"
                    + spec
                    + "\n\nCurrent code:\n```\n"
                    + code
                    + "\n```\n\nReview:\n"
                    + feedback
                    + "\n\nOutput the complete revised program only (one fenced block or raw code)."
                ),
            },
        ]
        raw_next = _chat(client, model, rev_messages, temperature, max_tokens_init)
        code = _extract_code(raw_next, lang)
        trace.append({"step": f"revise_{it}", "raw": raw_next, "code": code})

    return code, trace


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Self-Refine 风格 + DeepSeek，生成 DES 12 格代码（无本项目 PromptLoader）"
    )
    ap.add_argument(
        "--tasks",
        type=Path,
        default=ROOT / "experiments" / "rw_des_tasks.jsonl",
        help="rw_des_protocol_eval.py export 的 JSONL",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="写入 des_<mode>.<ext> 与可选 trace JSONL",
    )
    ap.add_argument(
        "--trace-jsonl",
        type=Path,
        default=None,
        help="若指定，追加每题迭代轨迹（调试）",
    )
    ap.add_argument("--model", type=str, default="deepseek-chat", help="DeepSeek 模型名")
    ap.add_argument(
        "--base-url",
        type=str,
        default="https://api.deepseek.com",
        help="OpenAI 兼容 API 根地址",
    )
    ap.add_argument(
        "--api-key-env",
        type=str,
        default="DEEPSEEK_API_KEY",
        help="读取 API Key 的环境变量名；回退尝试 OPENAI_API_KEY",
    )
    ap.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="总轮数：1 仅首轮生成；n>1 表示首轮 + (n-1) 次「自评+改写」",
    )
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens-init", type=int, default=4096)
    ap.add_argument("--max-tokens-feedback", type=int, default=1024)
    ap.add_argument("--sleep", type=float, default=0.0, help="每次 API 调用间隔（限速）")
    args = ap.parse_args()

    try:
        from openai import OpenAI
    except ImportError as e:
        print("[selfrefine_des_deepseek] 需要 openai>=1.0: pip install openai", file=sys.stderr)
        raise SystemExit(2) from e

    key = os.environ.get(args.api_key_env) or os.environ.get("OPENAI_API_KEY")
    if not key:
        print(
            f"[selfrefine_des_deepseek] 未设置 {args.api_key_env} 或 OPENAI_API_KEY",
            file=sys.stderr,
        )
        return 3

    client = OpenAI(api_key=key, base_url=args.base_url.rstrip("/"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = args.trace_jsonl
    if trace_path:
        trace_path.parent.mkdir(parents=True, exist_ok=True)

    rows = _load_tasks(args.tasks)
    for row in rows:
        spec = _task_spec(row)
        task_id = row.get("task_id", "?")
        fn = row.get("expected_filename")
        if not fn:
            print(f"[selfrefine_des_deepseek] 跳过（无 expected_filename）: {task_id}", file=sys.stderr)
            continue
        print(f"[selfrefine_des_deepseek] {task_id} …", file=sys.stderr)
        code, trace = _self_refine_loop(
            client,
            args.model,
            row,
            spec,
            max_iterations=max(1, args.max_iterations),
            temperature=args.temperature,
            max_tokens_init=args.max_tokens_init,
            max_tokens_fb=args.max_tokens_feedback,
            sleep_s=args.sleep,
        )
        dest = args.out_dir / fn
        dest.write_text(code, encoding="utf-8")
        print(f"[selfrefine_des_deepseek] -> {dest}", file=sys.stderr)

        if trace_path:
            rec = {"task_id": task_id, "expected_filename": fn, "trace": trace}
            with open(trace_path, "a", encoding="utf-8") as tf:
                tf.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(
        "[selfrefine_des_deepseek] 完成。打分: python experiments/related_work/rw_des_protocol_eval.py score "
        f"--inputs {args.out_dir} --arm selfrefine_deepseek --no-canonical-whole-file",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
