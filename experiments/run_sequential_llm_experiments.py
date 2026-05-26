#!/usr/bin/env python3
"""
论文相关实验 —— **顺序**跑完多步（与 Web 同源调用链）。

每一步内部都是：`CryptoAgent.generate_and_save` → `generate_code` → **`build_prompt`**
（按当前消融档位从 `prompts/` 加载对应 YAML 层）→ **请求 LLM** → 验证 / 测试。

默认顺序（可用 `--steps` 节选）：

1. **`run_paper_ablation --suite main`**：主消融（系统配置 × VPR/FTPR）
2. **`run_paper_ablation --suite prompt`**：提示策略消融（GSR/VPR/FTPR，与 main 同五行表）
3. **`run_prompt_ablation` 批量**：DES × **五家云端（openai/claude/deepseek/doubao/codex，config 已启用者）** × modes × langs；每格内对默认四档
   ``common_only`` / ``common_llm`` / ``common_llm_lang`` / ``full`` **依次**调用（若加 `--invoke`）；可用 `--skip-full-invoke` 跳过 full 档并从 Web 历史对齐

**须加 `--invoke` / `--live`** 才会在以上步骤中真实请求云端 LLM；否则第 1～2 步为 `--dry-run`（规模预览），
第 3 步为 **仅统计 prompt 长度**（不调模型）。

用法:

  # 真实跑完全部三步（不加 --provider 时与子脚本一致：默认五家云端已启用者；全网格仍可能耗时长）
  python experiments/run_sequential_llm_experiments.py --invoke \\
    --max-retries 3 -o-prefix exp_20260206

  # 试跑：单 provider + 截断格数
  python experiments/run_sequential_llm_experiments.py --invoke \\
    --provider deepseek --max-cases 1 --languages python --max-retries 3

  # 只做主消融 + 提示消融表，跳过第 3 步（四档 prompt 批量）
  python experiments/run_sequential_llm_experiments.py --invoke --steps main,prompt
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent


def _extras(args: argparse.Namespace) -> List[str]:
    out: List[str] = []
    if args.max_cases is not None:
        out.extend(["--max-cases", str(args.max_cases)])
    if args.provider:
        out.extend(["--provider", args.provider])
    if args.max_retries is not None:
        out.extend(["--max-retries", str(args.max_retries)])
    if args.config:
        out.extend(["--config", args.config])
    if args.modes:
        out.extend(["--modes", *args.modes])
    if args.languages:
        out.extend(["--languages", *args.languages])
    if args.verbose:
        out.append("--verbose")
    if getattr(args, "trace_prompt", False):
        out.append("--trace-prompt")
    if getattr(args, "skip_full_invoke", False):
        out.append("--skip-full-invoke")
    return out


def _run(label: str, cmd: List[str]) -> int:
    bar = "=" * 64
    print(f"\n{bar}\n[aicrypto-helper] {label}\n{bar}\n{shlex.join(cmd)}\n", file=sys.stderr)
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def main() -> None:
    ap = argparse.ArgumentParser(
        description="按顺序跑论文实验：每步内均为 build_prompt → LLM（与 Web 一致）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--invoke",
        "--live",
        action="store_true",
        help="三步中凡支持处均真实请求 LLM；省略则 paper 为 dry-run，prompt_ablation 仅长度统计",
    )
    ap.add_argument(
        "--steps",
        default="main,prompt,prompt-batch",
        help="逗号分隔：main | prompt | prompt-batch（默认三项全跑）",
    )
    ap.add_argument(
        "-o-prefix",
        "--output-prefix",
        default="sequential_exp",
        help="输出文件前缀：写入 experiments/results/{prefix}_*.md|json（默认 sequential_exp）",
    )
    ap.add_argument("--max-cases", type=int, default=None)
    ap.add_argument(
        "--provider",
        default=None,
        help="转发给子脚本；仅允许 openai/claude/deepseek/doubao/codex；省略则子脚本默认五家（已启用）",
    )
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--modes", nargs="*", default=None)
    ap.add_argument("--languages", nargs="*", default=None)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--trace-prompt",
        action="store_true",
        help="转发给子脚本：stderr 逐步打印每次 Prompt 加载与 LLM 调用",
    )
    ap.add_argument(
        "--skip-full-invoke",
        action="store_true",
        help="转发给 run_prompt_ablation：invoke 时跳过 full（本文方法），与 Web 历史对齐",
    )
    args = ap.parse_args()

    if args.provider:
        from experiments.ablation_defaults import ABLATION_EXPERIMENT_ALLOWED_PROVIDERS

        if args.provider.strip().lower() not in ABLATION_EXPERIMENT_ALLOWED_PROVIDERS:
            print(
                "顺序实验仅转发 openai / claude / deepseek / doubao / codex，请将 --provider 设为其中之一或省略。",
                file=sys.stderr,
            )
            sys.exit(2)

    py = sys.executable
    steps = {s.strip() for s in args.steps.split(",") if s.strip()}
    valid = {"main", "prompt", "prompt-batch"}
    bad = steps - valid
    if bad:
        print(f"未知 --steps: {bad}，允许 {valid}", file=sys.stderr)
        sys.exit(2)

    ex = _extras(args)
    prefix = args.output_prefix
    from experiments.experiment_outputs import experiments_results_dir

    res = experiments_results_dir()

    if "main" in steps:
        cmd = [
            py,
            str(ROOT / "experiments" / "run_paper_ablation.py"),
            "--suite",
            "main",
        ]
        if args.invoke:
            cmd.append("--invoke")
        else:
            cmd.append("--dry-run")
        cmd.extend(ex)
        cmd.extend(
            [
                "-o",
                str(res / f"{prefix}_paper_main.md"),
                "--json-output",
                str(res / f"{prefix}_paper_main.json"),
            ]
        )
        if _run("① 主消融（run_paper_ablation --suite main）", cmd) != 0:
            sys.exit(1)

    if "prompt" in steps:
        cmd = [
            py,
            str(ROOT / "experiments" / "run_paper_ablation.py"),
            "--suite",
            "prompt",
        ]
        if args.invoke:
            cmd.append("--invoke")
        else:
            cmd.append("--dry-run")
        cmd.extend(ex)
        cmd.extend(
            [
                "-o",
                str(res / f"{prefix}_paper_prompt.md"),
                "--json-output",
                str(res / f"{prefix}_paper_prompt.json"),
            ]
        )
        if _run("② 提示策略消融表（run_paper_ablation --suite prompt）", cmd) != 0:
            sys.exit(1)

    if "prompt-batch" in steps:
        cmd = [py, str(ROOT / "experiments" / "run_prompt_ablation.py")]
        if args.invoke:
            cmd.append("--invoke")
        cmd.extend(ex)
        cmd.extend(
            [
                "-o",
                str(res / f"{prefix}_prompt_batch.md"),
                "--json-output",
                str(res / f"{prefix}_prompt_batch.json"),
            ]
        )
        if _run(
            "③ 四档 prompt_ablation × 每格依次 generate_and_save（run_prompt_ablation 批量）",
            cmd,
        ) != 0:
            sys.exit(1)

    print(
        f"\n[aicrypto-helper] 顺序实验全部结束。输出目录：{res} ，前缀 `{prefix}_*`（md + json）。",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
