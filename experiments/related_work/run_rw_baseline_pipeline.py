#!/usr/bin/env python3
"""
相关工作 DES 12 格：一条命令串联 export →（可选）外部生成 → score。

本脚本**不**内置 SecCoder/SVEN 等仓库的推理逻辑；生成阶段仍由你在各基线官方环境
中实现的命令完成（与 ``rw_des_protocol_eval.py`` 文档一致）。本脚本只负责：

1. ``export``：写出 ``rw_des_tasks.jsonl``（可用 ``--skip-export`` 跳过）；
2. ``generator``：子进程执行你给的命令（须把 12 个 ``des_<mode>.{py,c,cpp}`` 写入 ``--out-dir``）；
3. ``score``：调用 ``rw_des_protocol_eval.py score``（默认带 ``--no-canonical-whole-file``，相关工作评测模型 C/C++ 原文）。

用法（bash / WSL）：生成命令写在 ``--`` 之后，避免与本脚本参数混淆。

  ROOT=/path/to/aicrypto-helper
  cd "$ROOT"

  python experiments/related_work/run_rw_baseline_pipeline.py \\
    --out-dir experiments/selfrefine_deepseek_out \\
    --arm selfrefine_deepseek \\
    --json-out experiments/rw_selfrefine_deepseek.json \\
    -- \\
    python experiments/related_work/selfrefine_des_deepseek.py \\
      --out-dir experiments/selfrefine_deepseek_out \\
      --max-iterations 3

仅当目录里已有 12 个文件、只想 export+score 时：

  python experiments/related_work/run_rw_baseline_pipeline.py \\
    --out-dir experiments/existing_out \\
    --arm my_tag \\
    --skip-generator

在子进程里需要 ``cd`` 到别的目录时，用 ``--generator-cwd`` 或包一层 shell：

  python experiments/related_work/run_rw_baseline_pipeline.py \\
    --out-dir experiments/sven_des_outputs \\
    --arm sven_manual \\
    --shell 'bash -lc "cd /path/to/sven/scripts && python human_eval_gen.py ... && ..."' \\
    --json-out experiments/rw_sven.json

Windows PowerShell 可把 ``--`` 后的命令写成一条 ``python ...`` 调用；若 shell 特性复杂，
优先在 WSL/bash 下跑 ``--shell``。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[2]


def _split_argv(argv: Sequence[str]) -> tuple[List[str], List[str]]:
    if "--" in argv:
        i = argv.index("--")
        return list(argv[:i]), list(argv[i + 1 :])
    return list(argv), []


def _run(cmd: List[str], *, cwd: Optional[Path], shell: bool) -> int:
    disp = cmd[0] if shell and cmd else cmd
    print(f"[run_rw_baseline_pipeline] 执行: {disp!r}  cwd={cwd}", file=sys.stderr)
    if shell:
        if len(cmd) != 1:
            raise ValueError("shell 模式仅支持单元素命令字符串")
        p = subprocess.run(cmd[0], shell=True, cwd=cwd)
    else:
        p = subprocess.run(cmd, cwd=cwd)
    return int(p.returncode)


def main() -> int:
    pipeline_argv, generator_argv = _split_argv(sys.argv[1:])
    ap = argparse.ArgumentParser(
        description="串联 export → 外部生成命令 → rw_des_protocol_eval score",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="12 个 des_<mode> 源文件所在目录（生成命令须写入此目录）",
    )
    ap.add_argument(
        "--arm",
        default="baseline",
        help="写入 JSON 的 arm 标签",
    )
    ap.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="score 输出 JSON 路径（默认 experiments/rw_<arm>.json）",
    )
    ap.add_argument(
        "--tasks-jsonl",
        type=Path,
        default=ROOT / "experiments" / "rw_des_tasks.jsonl",
        help="export 输出路径",
    )
    ap.add_argument(
        "--skip-export",
        action="store_true",
        help="跳过 export（tasks-jsonl 已存在时）",
    )
    ap.add_argument(
        "--skip-generator",
        action="store_true",
        help="不执行生成命令（目录已有文件时仅打分）",
    )
    ap.add_argument(
        "--generator-cwd",
        type=Path,
        default=None,
        help="生成子进程的工作目录（默认仓库根 ROOT）",
    )
    ap.add_argument(
        "--shell",
        metavar="CMD",
        default=None,
        help="整段 shell 命令（与 -- 后的 argv 二选一；Windows 可用 cmd 语法时自行包 bash -lc）",
    )
    ap.add_argument(
        "--canonical-whole-file",
        action="store_true",
        help="允许 C/C++ canonical OpenSSL 整文件替换（默认关闭，与相关工作文档一致）",
    )
    args = ap.parse_args(pipeline_argv)

    json_out = args.json_out
    if json_out is None:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.arm)
        json_out = ROOT / "experiments" / f"rw_{safe}.json"

    export_py = ROOT / "experiments" / "related_work" / "rw_des_protocol_eval.py"
    score_py = export_py
    gen_cwd = args.generator_cwd if args.generator_cwd is not None else ROOT

    if not args.skip_export:
        r = subprocess.run(
            [sys.executable, str(export_py), "export", "-o", str(args.tasks_jsonl)],
            cwd=ROOT,
        )
        if r.returncode != 0:
            return r.returncode

    if not args.skip_generator:
        if args.shell:
            if generator_argv:
                print(
                    "[run_rw_baseline_pipeline] 错误: 不能同时使用 --shell 与 -- 后的命令",
                    file=sys.stderr,
                )
                return 2
            rc = _run([args.shell], cwd=gen_cwd, shell=True)
            if rc != 0:
                return rc
        elif generator_argv:
            rc = _run(generator_argv, cwd=gen_cwd, shell=False)
            if rc != 0:
                return rc
        else:
            print(
                "[run_rw_baseline_pipeline] 警告: 未提供生成命令（--shell 或 -- 后子进程）；"
                "若 --out-dir 中尚无 12 个文件，score 将大量 missing",
                file=sys.stderr,
            )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    score_cmd: List[str] = [
        sys.executable,
        str(score_py),
        "score",
        "--inputs",
        str(args.out_dir),
        "--arm",
        args.arm,
        "-o",
        str(json_out),
    ]
    if not args.canonical_whole_file:
        score_cmd.append("--no-canonical-whole-file")
    r = subprocess.run(score_cmd, cwd=ROOT)
    return int(r.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
