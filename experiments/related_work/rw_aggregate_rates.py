#!/usr/bin/env python3
"""
从若干 ``rw_des_protocol_eval.py score`` 生成的 JSON 汇总 GSR / VPR / FTPR 表格。

每个 JSON 须含顶层 ``arm`` 与 ``rates_percent``（键 GSR、VPR、FTPR，单位为百分比数值）。

示例：

  python experiments/related_work/rw_aggregate_rates.py \\
    --preset related-work -o experiments/rw_rates_table.md

  python experiments/related_work/rw_aggregate_rates.py \\
    experiments/rw_sven.json experiments/rw_selfrefine_deepseek.json \\
    --format latex
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]

# preset：默认结果文件名（与各一键脚本 -o 约定一致）；缺失则跳过
PRESET_RELATED_WORK: List[Tuple[str, str]] = [
    ("experiments/rw_sven.json", "SVEN"),
    ("experiments/rw_selfrefine_deepseek.json", "Self-Refine + DeepSeek"),
    ("experiments/rw_seccoder.json", "SecCoder（复现）"),
    ("experiments/rw_agentcoder.json", "AgentCoder（复现）"),
]


def _load_rates(path: Path) -> Tuple[str, float, float, float]:
    with open(path, encoding="utf-8") as f:
        data: Dict[str, Any] = json.load(f)
    arm = str(data.get("arm", path.stem))
    rates = data.get("rates_percent") or {}
    try:
        gsr = float(rates["GSR"])
        vpr = float(rates["VPR"])
        ftpr = float(rates["FTPR"])
    except KeyError as e:
        raise KeyError(f"{path}: 缺少 rates_percent 字段 {e}") from e
    return arm, gsr, vpr, ftpr


def _rows_from_paths(
    paths: Sequence[Path],
    display_names: Optional[Dict[str, str]] = None,
) -> List[Tuple[str, float, float, float]]:
    rows: List[Tuple[str, float, float, float]] = []
    display_names = display_names or {}
    for p in paths:
        arm, gsr, vpr, ftpr = _load_rates(p)
        label = display_names.get(str(p).replace("\\", "/"), arm)
        rows.append((label, gsr, vpr, ftpr))
    return rows


def _format_markdown(rows: List[Tuple[str, float, float, float]]) -> str:
    lines = [
        "| 基线 | GSR (%) | VPR (%) | FTPR (%) |",
        "| :--- | :---: | :---: | :---: |",
    ]
    for name, g, v, f in rows:
        lines.append(f"| {name} | {g:.2f} | {v:.2f} | {f:.2f} |")
    return "\n".join(lines) + "\n"


def _format_latex(rows: List[Tuple[str, float, float, float]]) -> str:
    """booktabs 一行一个基线，自行包 \\begin{tabular}。"""
    parts = []
    for name, g, v, f in rows:
        safe = name.replace("&", r"\&").replace("%", r"\%")
        parts.append(f"{safe} & {g:.2f} & {v:.2f} & {f:.2f} \\\\")
    return "\n".join(parts) + "\n"


def _format_tsv(rows: List[Tuple[str, float, float, float]]) -> str:
    lines = ["baseline\tGSR\tVPR\tFTPR"]
    for name, g, v, f in rows:
        lines.append(f"{name}\t{g:.2f}\t{v:.2f}\t{f:.2f}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="汇总 rw_*.json 的 GSR/VPR/FTPR 表格")
    ap.add_argument(
        "json_files",
        nargs="*",
        type=Path,
        help="score 输出的 JSON 路径（与 --preset 二选一或叠加）",
    )
    ap.add_argument(
        "--preset",
        choices=("related-work",),
        default=None,
        help="使用内置默认结果文件列表（存在的才纳入）",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="写入文件；默认只打印到 stdout",
    )
    ap.add_argument(
        "--format",
        choices=("markdown", "latex", "tsv"),
        default="markdown",
    )
    args = ap.parse_args()

    paths: List[Path] = []
    display: Dict[str, str] = {}

    if args.preset == "related-work":
        for rel, title in PRESET_RELATED_WORK:
            p = ROOT / rel
            if p.is_file():
                paths.append(p)
                display[str(p.resolve())] = title
            else:
                print(f"[rw_aggregate_rates] 跳过（文件不存在）: {p}", file=sys.stderr)

    for p in args.json_files:
        pp = p if p.is_absolute() else ROOT / p
        if not pp.is_file():
            print(f"[rw_aggregate_rates] 跳过（文件不存在）: {pp}", file=sys.stderr)
            continue
        paths.append(pp.resolve())

    if not paths:
        print("[rw_aggregate_rates] 没有可用的 JSON，请先运行各基线一键脚本或 score。", file=sys.stderr)
        return 2

    # 按路径去重，保留首次出现顺序
    seen = set()
    uniq: List[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(rp)

    rows = _rows_from_paths(uniq, display_names=display)
    if args.format == "markdown":
        text = _format_markdown(rows)
    elif args.format == "latex":
        text = _format_latex(rows)
    else:
        text = _format_tsv(rows)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"[rw_aggregate_rates] 已写入 {args.output}", file=sys.stderr)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
