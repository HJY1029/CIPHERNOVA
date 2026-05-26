#!/usr/bin/env python3
"""
读取 ``llm_performance.json``，按 ``classify_record`` 四类统计失败记录数量、占比、
失败子集中「本条 test_success===true」的比例（修复前通过率），以及
``error_repair_aggregate`` 口径下的「后续日志出现成功」比例（修复后通过率）。

用法（项目根目录）：
  python experiments/history_repair_success_table.py
  python experiments/history_repair_success_table.py --performance-json ./llm_performance.json
  python experiments/history_repair_success_table.py --latex
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.error_repair_aggregate import (  # noqa: E402
    CAT_ORDER,
    _build_case_lists_sorted,
    _is_failure_record,
    _repair_resolved_after,
    classify_record,
    flatten_performance_records,
)

DEFAULT_PERF = ROOT / "llm_performance.json"


def _aggregate_failure_resolution_from_flat(
    flat: List[Dict[str, Any]],
) -> Tuple[Dict[str, int], Dict[str, int], int]:
    by_case = _build_case_lists_sorted(flat)

    def _pass(x: Dict[str, Any]) -> bool:
        return x.get("test_success") is True

    failures = [r for r in flat if _is_failure_record(r)]
    fail_by_cat: Dict[str, int] = {k: 0 for k in CAT_ORDER}
    resolved_perf_by_cat: Dict[str, int] = {k: 0 for k in CAT_ORDER}
    for r in failures:
        cat = classify_record(r)
        if cat in fail_by_cat:
            fail_by_cat[cat] += 1
        if _repair_resolved_after(r, by_case, _pass):
            if cat in resolved_perf_by_cat:
                resolved_perf_by_cat[cat] += 1
    fail_total = sum(fail_by_cat.values())
    return fail_by_cat, resolved_perf_by_cat, fail_total


def _pass_before_pct_for_failures(flat: List[Dict[str, Any]]) -> Dict[str, float]:
    """同类失败中本条 test_success===true 的比例（与聚合脚本一致）。"""
    fails = [r for r in flat if _is_failure_record(r)]
    by_cat: Dict[str, List[Dict[str, Any]]] = {k: [] for k in CAT_ORDER}
    for r in fails:
        by_cat.setdefault(classify_record(r), []).append(r)
    out: Dict[str, float] = {}
    for cat in CAT_ORDER:
        lst = by_cat.get(cat) or []
        if not lst:
            out[cat] = 0.0
            continue
        pb = sum(1 for r in lst if r.get("test_success") is True)
        out[cat] = round(100.0 * pb / len(lst), 2)
    return out


def render_markdown(perf_path: Path) -> str:
    raw = json.loads(perf_path.read_text(encoding="utf-8"))
    flat = flatten_performance_records(raw)
    pb_cat = _pass_before_pct_for_failures(flat)
    fail_by_cat, resolved_perf_by_cat, fail_total = _aggregate_failure_resolution_from_flat(flat)

    lines: List[str] = []
    lines.append("### 表：错误类型 × llm_performance 失败池与修复后通过率\n")
    lines.append("")
    lines.append(
        f"*数据源：`{perf_path.name}`；「修复后通过率」与 ``error_repair_aggregate`` 一致（同 case 后续日志出现成功）。*"
    )
    lines.append("")

    headers = ["错误类型", "数量", "占比", "修复前通过率", "修复后通过率"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join([":---"] + [":---:"] * (len(headers) - 1)) + " |")

    for cat in CAT_ORDER:
        n = fail_by_cat.get(cat, 0)
        sh = round(100.0 * n / fail_total, 2) if fail_total else 0.0
        pw = pb_cat.get(cat, 0.0)
        pa_perf = round(100.0 * resolved_perf_by_cat.get(cat, 0) / n, 2) if n else None
        row = [cat, str(n), f"{sh:.2f}%", f"{pw:.1f}%"]
        row.append("—" if pa_perf is None else f"{pa_perf:.2f}%")
        lines.append("| " + " | ".join(row) + " |")

    n_tot = fail_total
    pb_tot = round(
        100.0
        * sum(1 for r in flat if _is_failure_record(r) and r.get("test_success") is True)
        / max(len([r for r in flat if _is_failure_record(r)]), 1),
        2,
    )
    rp_perf_tot = (
        round(100.0 * sum(resolved_perf_by_cat.values()) / n_tot, 2) if n_tot else None
    )

    row_tot = ["**合计**", f"**{n_tot}**", "**100.0%**", f"**{pb_tot:.1f}%**"]
    row_tot.append("—" if rp_perf_tot is None else f"**{rp_perf_tot:.2f}%**")
    lines.append("| " + " | ".join(row_tot) + " |")
    lines.append("")
    return "\n".join(lines)


def render_latex_table(perf_path: Path) -> str:
    raw = json.loads(perf_path.read_text(encoding="utf-8"))
    flat = flatten_performance_records(raw)
    pb_cat = _pass_before_pct_for_failures(flat)
    fail_by_cat, resolved_perf_by_cat, fail_total = _aggregate_failure_resolution_from_flat(flat)

    out: List[str] = []
    out.append(r"\begin{tabular}{lcccc}")
    out.append(r"\toprule")
    out.append(r"错误类型 & 数量 & 占比 & 修复前通过率 & 修复后通过率 \\")
    out.append(r"\midrule")
    for cat in CAT_ORDER:
        n = fail_by_cat.get(cat, 0)
        sh = round(100.0 * n / fail_total, 2) if fail_total else 0.0
        pw = pb_cat.get(cat, 0.0)
        pa_perf = round(100.0 * resolved_perf_by_cat.get(cat, 0) / n, 2) if n else 0.0
        out.append(
            f"{cat} & {n} & {sh:.2f}\\% & {pw:.1f}\\% & {pa_perf:.2f}\\% \\\\"
        )
    pb_tot = round(
        100.0
        * sum(1 for r in flat if _is_failure_record(r) and r.get("test_success") is True)
        / max(len([r for r in flat if _is_failure_record(r)]), 1),
        2,
    )
    rp_perf_tot = round(100.0 * sum(resolved_perf_by_cat.values()) / fail_total, 2) if fail_total else 0.0
    out.append(
        f"\\midrule\n合计 & {fail_total} & 100.0\\% & {pb_tot:.1f}\\% & {rp_perf_tot:.2f}\\% \\\\"
    )
    out.append(r"\bottomrule\end{tabular}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="llm_performance 错误类型 × 修复后通过率表")
    ap.add_argument("--performance-json", type=Path, default=DEFAULT_PERF)
    ap.add_argument("--latex", action="store_true", help="输出 LaTeX tabular 片段")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    if not args.performance_json.is_file():
        print(f"错误: 找不到 {args.performance_json}", file=sys.stderr)
        return 2

    if args.latex:
        print(render_latex_table(args.performance_json))
    else:
        print(render_markdown(args.performance_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
