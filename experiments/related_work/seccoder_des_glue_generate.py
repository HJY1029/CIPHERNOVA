#!/usr/bin/env python3
"""
SecCoder **风格复现用**胶水：可选「安全代码检索片段」+ 中性任务说明 → OpenAI 兼容 **代码 LM** →
写入 ``rw_des_tasks.jsonl`` 每行的 ``expected_filename``。

**不是** ACL 附件里的官方可执行入口；**不**使用本仓库 ``PromptLoader`` / ``CryptoAgent`` / ``prompts/``。
若论文/实验中将产物标为 SecCoder 复现，须在正文写明：检索口径（本脚本 ``random_file`` 或你自接
``retriever_security.py`` 产物）、所用模型与 API。

可选检索模式：
  - 默认 ``none``：不注入片段（等价于无检索基线）。
  - ``random_file``：从 ``--security-snippets-dir`` 下随机（按 ``task_id`` 固定种子）选若干源码文件，
    拼接为「检索到的上下文」（**占位**，与论文 BM25/Instructor 不等价；真要对齐请自接附件脚本输出）。

用法示例（OpenAI 兼容端点，``pip install openai``）：

  # DeepSeek（与 selfrefine_des_deepseek.py 一致：官方 OpenAI 兼容 API）
  export DEEPSEEK_API_KEY=sk-...
  python experiments/related_work/seccoder_des_glue_generate.py \\
    --provider deepseek \\
    --out-dir experiments/seccoder_des_out \\
    --retrieval-mode random_file \\
    --security-snippets-dir external/SecCoder_acl_security_snippets

  # OpenAI
  export OPENAI_API_KEY=sk-...
  python experiments/related_work/seccoder_des_glue_generate.py \\
    --provider openai \\
    --out-dir experiments/seccoder_des_out \\
    --retrieval-mode random_file \\
    --security-snippets-dir external/SecCoder_acl_security_snippets

  bash experiments/related_work/run_rw_seccoder_des.sh

  # 一键流水线（默认 DeepSeek；改用 OpenAI 时 export SECCODER_LM_BACKEND=openai）：
  #   export DEEPSEEK_API_KEY=sk-...
  #   bash experiments/related_work/run_seccoder_des_lm_pipeline.sh
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# (base_url, default_model, default_api_key_env) — 与 selfrefine_des_deepseek 中 DeepSeek 默认一致
PROVIDER_PRESETS = {
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "deepseek-chat", "DEEPSEEK_API_KEY"),
}

_CANDIDATE_SUFFIXES = (
    ".py",
    ".c",
    ".cpp",
    ".h",
    ".java",
    ".go",
    ".txt",
)


def _task_spec(row: Dict[str, Any]) -> str:
    """与 selfrefine_des_deepseek 一致的中性规范（非 prompts/）。"""
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


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _collect_snippet_files(d: Path) -> List[Path]:
    files: List[Path] = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix.lower() in _CANDIDATE_SUFFIXES:
            files.append(p)
    return files


def _build_retrieval_block(
    mode: str,
    security_dir: Optional[Path],
    task_id: str,
    *,
    num_files: int,
    max_chars_per_file: int,
) -> str:
    if mode == "none":
        return ""
    if mode != "random_file":
        raise ValueError(f"unknown retrieval mode: {mode}")
    if not security_dir or not security_dir.is_dir():
        print(
            "[seccoder_des_glue] random_file 需要存在的 --security-snippets-dir",
            file=sys.stderr,
        )
        return ""

    files = _collect_snippet_files(security_dir)
    if not files:
        print(
            f"[seccoder_des_glue] {security_dir} 下无可用片段文件（后缀 {_CANDIDATE_SUFFIXES}）",
            file=sys.stderr,
        )
        return ""

    rng = random.Random(hash(task_id) % (2**32))
    picks = (
        rng.sample(files, k=min(num_files, len(files)))
        if len(files) >= num_files
        else list(files)
    )
    parts: List[str] = []
    for fp in picks:
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(raw) > max_chars_per_file:
            raw = raw[:max_chars_per_file] + "\n/* ... truncated ... */\n"
        parts.append(f"### File: {fp.name}\n```\n{raw}\n```\n")
    if not parts:
        return ""
    return (
        "## Retrieved security-related code snippets (reference only; do not copy insecure patterns)\n"
        + "\n".join(parts)
    )


def _full_user_prompt(spec: str, retrieval_block: str) -> str:
    if retrieval_block:
        return (
            f"{retrieval_block}\n\n"
            "## Task\n"
            f"{spec}\n\n"
            "Output only the complete runnable program for the task (one markdown fenced code block or raw code)."
        )
    return (
        "## Task\n"
        f"{spec}\n\n"
        "Output only the complete runnable program (one markdown fenced code block or raw code)."
    )


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


def main() -> int:
    ap = argparse.ArgumentParser(
        description="SecCoder 风格：可选检索片段 + 代码 LM → rw_des_tasks 的 12 个文件名（无 PromptLoader）",
    )
    ap.add_argument(
        "--tasks",
        type=Path,
        default=ROOT / "experiments" / "rw_des_tasks.jsonl",
    )
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument(
        "--retrieval-mode",
        choices=("none", "random_file"),
        default="none",
        help="none=不注入片段；random_file=从目录按 task_id 固定随机选文件（占位，非附件 BM25）",
    )
    ap.add_argument(
        "--security-snippets-dir",
        type=Path,
        default=None,
        help="random_file 时：含若干源码文件的目录（如 SecCoder_acl_security_snippets）",
    )
    ap.add_argument("--num-snippet-files", type=int, default=1, help="random_file 每题抽取文件数上限")
    ap.add_argument(
        "--max-chars-per-snippet",
        type=int,
        default=6000,
        help="每个片段文件最多字符数，防止撑爆上下文",
    )
    ap.add_argument(
        "--provider",
        choices=tuple(PROVIDER_PRESETS.keys()),
        default="openai",
        help="openai 或 deepseek（预设 base-url / 模型 / Key 环境变量名，可用下方三参数覆盖）",
    )
    ap.add_argument("--model", type=str, default=None, help="覆盖 --provider 默认模型")
    ap.add_argument("--base-url", type=str, default=None, help="覆盖 --provider 默认 API 根地址")
    ap.add_argument(
        "--api-key-env",
        type=str,
        default=None,
        help="从该环境变量读 Key；默认随 --provider；仍回退尝试 OPENAI_API_KEY / DEEPSEEK_API_KEY",
    )
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印第一题 user prompt 并退出（不调 API）",
    )
    args = ap.parse_args()

    b_def, m_def, e_def = PROVIDER_PRESETS[args.provider]
    base_url = args.base_url if args.base_url is not None else b_def
    model = args.model if args.model is not None else m_def
    api_key_env = args.api_key_env if args.api_key_env is not None else e_def

    try:
        from openai import OpenAI
    except ImportError as e:
        hint = "pip install -U 'openai>=1.0'"
        try:
            import openai as _oa

            ver = getattr(_oa, "__version__", "?")
            hint = (
                f"当前环境里 openai=={ver} 为旧版 SDK（0.x），与本脚本使用的 "
                f"`OpenAI` 客户端不兼容。在同一 venv 执行: pip install -U 'openai>=1.0'"
            )
        except Exception:
            hint = "pip install -U 'openai>=1.0'"
        print(f"[seccoder_des_glue] {hint}", file=sys.stderr)
        raise SystemExit(2) from e

    rows = _load_tasks(args.tasks)
    if not rows:
        print("[seccoder_des_glue] tasks 为空", file=sys.stderr)
        return 2

    first = rows[0]
    tid0 = first.get("task_id", "?")
    rb0 = _build_retrieval_block(
        args.retrieval_mode,
        args.security_snippets_dir,
        tid0,
        num_files=max(1, args.num_snippet_files),
        max_chars_per_file=max(500, args.max_chars_per_snippet),
    )
    prompt0 = _full_user_prompt(_task_spec(first), rb0)
    if args.dry_run:
        print(prompt0)
        return 0

    key = (
        os.environ.get(api_key_env)
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
    )
    if not key:
        print(
            f"[seccoder_des_glue] 未设置 {api_key_env}（或回退 OPENAI_API_KEY / DEEPSEEK_API_KEY）",
            file=sys.stderr,
        )
        return 3

    client = OpenAI(api_key=key, base_url=base_url.rstrip("/"))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    import time

    for row in rows:
        task_id = row.get("task_id", "?")
        fn = row.get("expected_filename")
        if not fn:
            print(f"[seccoder_des_glue] 跳过（无 expected_filename）: {task_id}", file=sys.stderr)
            continue
        lang = (row.get("language") or "python").lower()
        spec = _task_spec(row)
        rb = _build_retrieval_block(
            args.retrieval_mode,
            args.security_snippets_dir,
            task_id,
            num_files=max(1, args.num_snippet_files),
            max_chars_per_file=max(500, args.max_chars_per_snippet),
        )
        user_content = _full_user_prompt(spec, rb)
        messages = [
            {
                "role": "system",
                "content": (
                    "You write concise, runnable cryptographic utility programs. "
                    "Follow the I/O contract. Snippets are for context only."
                ),
            },
            {"role": "user", "content": user_content},
        ]
        print(f"[seccoder_des_glue] {task_id} -> {fn} …", file=sys.stderr)
        raw = _chat(client, model, messages, args.temperature, args.max_tokens)
        code = _extract_code(raw, lang)
        dest = args.out_dir / fn
        dest.write_text(code, encoding="utf-8")
        print(f"[seccoder_des_glue] wrote {dest}", file=sys.stderr)
        if args.sleep > 0:
            time.sleep(args.sleep)

    print(
        "[seccoder_des_glue] 完成。打分: bash experiments/related_work/run_rw_seccoder_des.sh\n"
        "  或: python experiments/related_work/rw_des_protocol_eval.py score "
        f"--inputs {args.out_dir} --arm seccoder_glue_repro --no-canonical-whole-file "
        "-o experiments/rw_seccoder.json",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
