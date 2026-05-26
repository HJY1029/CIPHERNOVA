#!/usr/bin/env python3
"""
AgentCoder **仅 Programmer 阶段**复现用胶水（与 ``seccoder_des_glue_generate.py`` 同风格）：

- 读 ``rw_des_tasks.jsonl`` → 中性任务说明（与 selfrefine / SecCoder 胶水一致）→ OpenAI 兼容 **chat.completions**
- 可选：与 SecCoder 相同的 ``random_file`` 检索占位、``--provider deepseek|openai``
- 可选：拼接上游 ``external/AgentCoder/prompts/humaneval_prompt_update.txt`` 作为 **Programmer** few-shot（与 ``programmer_humaneval.py`` 同一文件）

**不是**跑通 ``test_designer_humaneval`` / ``test_executor_humaneval`` 全链路；**不**使用本仓库 ``PromptLoader`` / ``CryptoAgent`` / ``prompts/``。
若论文中记为 AgentCoder 复现，须写明：仅用 Programmer 式单轮（或自接多 Agent）、模型与 API。

用法：

  export DEEPSEEK_API_KEY=sk-...
  python experiments/related_work/rw_des_protocol_eval.py export -o experiments/rw_des_tasks.jsonl

  python experiments/related_work/agentcoder_des_programmer_glue.py \\
    --provider deepseek \\
    --out-dir experiments/agentcoder_des_out \\
    --retrieval-mode none

  bash experiments/related_work/run_rw_agentcoder_des.sh
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_seccoder_glue_module():
    path = Path(__file__).resolve().parent / "seccoder_des_glue_generate.py"
    spec = importlib.util.spec_from_file_location("seccoder_des_glue_generate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 seccoder_des_glue_generate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_sg = _load_seccoder_glue_module()
_task_spec = _sg._task_spec
_extract_code = _sg._extract_code
_load_tasks = _sg._load_tasks
_build_retrieval_block = _sg._build_retrieval_block
_chat = _sg._chat
PROVIDER_PRESETS = _sg.PROVIDER_PRESETS


def _fence_tag(language: str) -> str:
    lang = language.lower()
    if lang == "cpp":
        return "cpp"
    if lang == "c":
        return "c"
    return "python"


def _build_programmer_user_prompt(
    spec: str,
    language: str,
    retrieval_block: str,
    few_shot_path: Optional[Path],
    use_few_shot: bool,
) -> str:
    """模仿 programmer_humaneval：few-shot + Input Code Snippet + Completion；目标语言由任务指定。"""
    fence = _fence_tag(language)
    parts: List[str] = []
    if retrieval_block:
        parts.append(retrieval_block.strip())
        parts.append("")

    if use_few_shot and few_shot_path is not None and few_shot_path.is_file():
        few = few_shot_path.read_text(encoding="utf-8", errors="replace").rstrip()
        parts.append(few)
        parts.append(
            f"**Note**: The examples above are mostly Python stubs; **this task** requires a "
            f"complete single-file program in **{language}** (not a HumanEval function fragment only).\n"
        )

    commented = "\n".join(f"# {line}" for line in spec.split("\n"))
    parts.append(
        "**Input Code Snippet** (requirements as comments — implement the full program):\n"
        f"```{fence}\n{commented}\n```\n\n"
        "## Completion:\n"
        f"Provide the full {language} source in one markdown fenced code block (```{fence} ... ```)."
    )
    return "\n\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="AgentCoder Programmer 阶段风格：DES 12 格 + OpenAI 兼容 LM（无 PromptLoader）",
    )
    default_few = ROOT / "external" / "AgentCoder" / "prompts" / "humaneval_prompt_update.txt"
    ap.add_argument(
        "--tasks",
        type=Path,
        default=ROOT / "experiments" / "rw_des_tasks.jsonl",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "experiments" / "agentcoder_des_out",
        help="默认 experiments/agentcoder_des_out",
    )
    ap.add_argument(
        "--retrieval-mode",
        choices=("none", "random_file"),
        default="none",
    )
    ap.add_argument("--security-snippets-dir", type=Path, default=None)
    ap.add_argument("--num-snippet-files", type=int, default=1)
    ap.add_argument("--max-chars-per-snippet", type=int, default=6000)
    ap.add_argument(
        "--agentcoder-prompt",
        type=Path,
        default=default_few,
        help="Programmer few-shot 文本（Humaneval）；不存在则仅用语义块",
    )
    ap.add_argument(
        "--no-few-shot",
        action="store_true",
        help="不拼接 AgentCoder 的 humaneval_prompt_update.txt",
    )
    ap.add_argument(
        "--provider",
        choices=tuple(PROVIDER_PRESETS.keys()),
        default="openai",
    )
    ap.add_argument("--model", type=str, default=None)
    ap.add_argument("--base-url", type=str, default=None)
    ap.add_argument("--api-key-env", type=str, default=None)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    b_def, m_def, e_def = PROVIDER_PRESETS[args.provider]
    base_url = args.base_url if args.base_url is not None else b_def
    model = args.model if args.model is not None else m_def
    api_key_env = args.api_key_env if args.api_key_env is not None else e_def

    try:
        from openai import OpenAI
    except ImportError:
        hint = "pip install -U 'openai>=1.0'"
        try:
            import openai as _oa

            ver = getattr(_oa, "__version__", "?")
            hint = (
                f"当前 openai=={ver} 为旧版（0.x）。请执行: pip install -U 'openai>=1.0'"
            )
        except Exception:
            pass
        print(f"[agentcoder_des_programmer_glue] {hint}", file=sys.stderr)
        return 2

    rows = _load_tasks(args.tasks)
    if not rows:
        print("[agentcoder_des_programmer_glue] tasks 为空", file=sys.stderr)
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
    lang0 = (first.get("language") or "python").lower()
    u0 = _build_programmer_user_prompt(
        _task_spec(first),
        lang0,
        rb0,
        args.agentcoder_prompt,
        use_few_shot=not args.no_few_shot,
    )
    if args.dry_run:
        print(u0)
        return 0

    key = (
        os.environ.get(api_key_env)
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
    )
    if not key:
        print(
            f"[agentcoder_des_programmer_glue] 未设置 {api_key_env}（或 OPENAI_API_KEY / DEEPSEEK_API_KEY）",
            file=sys.stderr,
        )
        return 3

    client = OpenAI(api_key=key, base_url=base_url.rstrip("/"))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for row in rows:
        task_id = row.get("task_id", "?")
        fn = row.get("expected_filename")
        if not fn:
            print(f"[agentcoder_des_programmer_glue] 跳过: {task_id}", file=sys.stderr)
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
        user_content = _build_programmer_user_prompt(
            spec,
            lang,
            rb,
            args.agentcoder_prompt,
            use_few_shot=not args.no_few_shot,
        )
        messages = [
            {"role": "system", "content": "You are a software programmer."},
            {"role": "user", "content": user_content},
        ]
        print(f"[agentcoder_des_programmer_glue] {task_id} -> {fn} …", file=sys.stderr)
        raw = _chat(client, model, messages, args.temperature, args.max_tokens)
        code = _extract_code(raw, lang)
        dest = args.out_dir / fn
        dest.write_text(code, encoding="utf-8")
        print(f"[agentcoder_des_programmer_glue] wrote {dest}", file=sys.stderr)
        if args.sleep > 0:
            time.sleep(args.sleep)

    print(
        "[agentcoder_des_programmer_glue] 完成。打分: bash experiments/related_work/run_rw_agentcoder_des.sh\n"
        "  或: python experiments/related_work/rw_des_protocol_eval.py score "
        f"--inputs {args.out_dir} --arm agentcoder_des --no-canonical-whole-file "
        "-o experiments/rw_agentcoder.json",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
