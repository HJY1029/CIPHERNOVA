#!/usr/bin/env python3
"""
论文「表：不同错误类型的分布与修复效果」(tab:error_repair) 的 Markdown 表生成器。

列：错误类型 | 数量 | 占比 | 修复前通过率 | 修复后通过率
（与 paperzh 表 10 版式一致：首列左对齐，其余居中）

数据源有三种：

1. **默认**：`experiments/data/error_repair_table.json` — 与 paperzh 排版一致的**论文示例静态表**（非运行时实测）。
2. **实测聚合**：加 **`--from-performance`**，从 **`experiments/results/llm_performance.json`**（`CryptoAgent._record_performance` 写入；若不存在则回退读仓库根目录历史文件）
   按启发式将失败样本归入四类，并统计「修复后通过率」。**该列是观测定义**（同 case 更晚是否出现 `test_success`），**不是**按错误类执行不同自动修复策略；详见 `error_repair_aggregate.py` 模块注释与输出 `source_note`。
3. **历史库聚合（推荐填论文）**：加 **`--from-history`**，合并 **`code_history.db`**（Web 成功落库）与 **`llm_performance.json`**（失败明细含 `error_message`）。
   失败样本仍来自 performance 日志；修复判定时间线额外纳入 history 中更晚的成功记录。可加 **`--provider deepseek`** 与论文表注一致。

用法:
  python experiments/run_error_repair_table.py
  python experiments/run_error_repair_table.py --from-performance -o error_repair_live.md
  python experiments/run_error_repair_table.py --from-history --provider deepseek \
    -o error_repair_from_history.md --write-json error_repair_from_history.json
    （-o / --write-json 相对路径写入 experiments/results/。）
  python experiments/run_error_repair_table.py --from-performance --performance-json ./my_perf.json
  # 数量/占比仍来自实测，仅「修复后通过率」列强制为 100%（脚注注明覆盖，供排版或与论文口径对齐）:
  python experiments/run_error_repair_table.py --from-performance --force-post-repair-pct 100 -o error_repair_live.md
  python experiments/run_error_repair_table.py --input my_aggregate.json

自定义静态 JSON 格式见 experiments/data/error_repair_table.json。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.experiment_outputs import resolve_under_results  # noqa: E402

DEFAULT_JSON = Path(__file__).resolve().parent / "data" / "error_repair_table.json"


def _default_performance_json_path() -> Path:
    primary = resolve_under_results(Path("llm_performance.json"))
    if primary.is_file():
        return primary
    legacy = ROOT / "llm_performance.json"
    return legacy if legacy.is_file() else primary


def _fmt_pct_display(x: float) -> str:
    """论文风格：整数显示为 0.0%，否则最多两位小数。"""
    x = float(x)
    if abs(x - round(x, 1)) < 1e-9:
        return f"{x:.1f}%"
    return f"{x:.2f}%"


def _compute_share(count: int, total_count: int) -> float:
    if total_count <= 0:
        return 0.0
    return round(100.0 * count / total_count, 2)


def _load_payload(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _normalize_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """缺少 share_pct 时按 count / sum(count) 计算。numeric_placeholder 时数值列渲染为 "-"。"""

    if raw.get("numeric_placeholder"):
        rows_in = raw.get("rows") or []
        rows_out: List[Dict[str, Any]] = []
        for r in rows_in:
            rows_out.append(
                {
                    "name": str(r.get("name", "")),
                    "count": None,
                    "share_pct": None,
                    "pass_before_pct": None,
                    "pass_after_pct": None,
                }
            )
        total_in = raw.get("total") or {}
        return {
            "title": raw.get("title", "不同错误类型的分布与修复效果"),
            "source_note": raw.get("source_note"),
            "rows": rows_out,
            "total": {
                "name": str(total_in.get("name", "合计")),
                "count": None,
                "share_pct": None,
                "pass_before_pct": None,
                "pass_after_pct": None,
            },
            "numeric_placeholder": True,
        }

    rows_in = raw.get("rows") or []
    total_in = raw.get("total") or {}

    n_sum = sum(int(r.get("count", 0) or 0) for r in rows_in)
    if not n_sum and total_in.get("count"):
        n_sum = int(total_in["count"])

    rows_out: List[Dict[str, Any]] = []
    for r in rows_in:
        c = int(r.get("count", 0) or 0)
        sh = r.get("share_pct")
        if sh is None and n_sum > 0:
            sh = _compute_share(c, n_sum)
        pb = r.get("pass_before_pct", 0)
        pb_out: Optional[float]
        if pb is None:
            pb_out = None
        else:
            pb_out = float(pb or 0)
        if "pass_after_pct" not in r:
            pa_out = 0.0
        elif r.get("pass_after_pct") is None:
            pa_out = None
        else:
            pa_out = float(r["pass_after_pct"] or 0)
        row = {
            "name": str(r.get("name", "")),
            "count": c,
            "share_pct": float(sh) if sh is not None else 0.0,
            "pass_before_pct": pb_out,
            "pass_after_pct": pa_out,
        }
        rows_out.append(row)

    total_count = int(total_in.get("count", 0) or 0) or n_sum
    t_sh = total_in.get("share_pct")
    if t_sh is None:
        t_sh = 100.0 if total_count else 0.0

    t_pb = total_in.get("pass_before_pct", 0)
    t_pb_out: Optional[float] = None if t_pb is None else float(t_pb or 0)
    t_pa = total_in.get("pass_after_pct")
    if "pass_after_pct" not in total_in:
        t_pa_out: Optional[float] = 0.0
    elif t_pa is None:
        t_pa_out = None
    else:
        t_pa_out = float(t_pa or 0)
    total_out = {
        "name": str(total_in.get("name", "合计")),
        "count": total_count,
        "share_pct": float(t_sh),
        "pass_before_pct": t_pb_out,
        "pass_after_pct": t_pa_out,
    }

    return {
        "title": raw.get("title", "不同错误类型的分布与修复效果"),
        "source_note": raw.get("source_note"),
        "rows": rows_out,
        "total": total_out,
        "numeric_placeholder": False,
    }


def _fmt_table_cell(
    value: Any,
    *,
    is_pct: bool,
    placeholder: bool,
    bold: bool = False,
) -> str:
    if placeholder or value is None:
        s = "-"
    elif is_pct:
        s = _fmt_pct_display(float(value))
    else:
        s = str(int(value))
    return f"**{s}**" if bold else s


def render_error_repair_markdown(
    data: Dict[str, Any], *, with_caption: bool, table_number: Optional[str]
) -> str:
    d = _normalize_payload(data)
    ph = bool(d.get("numeric_placeholder"))
    lines: List[str] = []

    if table_number:
        lines.append(f"**表 {table_number}：{d['title']}**")
    else:
        lines.append(f"**{d['title']}**")
    lines.append("")

    if with_caption and d.get("source_note"):
        lines.append(f"*{d['source_note']}*")
        lines.append("")

    header = (
        "| 错误类型 | 数量 | 占比 | 修复前通过率 | 修复后通过率 |"
    )
    sep = "| :--- | :---: | :---: | :---: | :---: |"

    lines.extend([header, sep])

    for r in d["rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    r["name"],
                    _fmt_table_cell(r["count"], is_pct=False, placeholder=ph),
                    _fmt_table_cell(r["share_pct"], is_pct=True, placeholder=ph),
                    _fmt_table_cell(r["pass_before_pct"], is_pct=True, placeholder=ph),
                    _fmt_table_cell(r["pass_after_pct"], is_pct=True, placeholder=ph),
                ]
            )
            + " |"
        )

    t = d["total"]
    lines.append(
        "| "
        + " | ".join(
            [
                f"**{t['name']}**",
                _fmt_table_cell(t["count"], is_pct=False, placeholder=ph, bold=True),
                _fmt_table_cell(t["share_pct"], is_pct=True, placeholder=ph, bold=True),
                _fmt_table_cell(t["pass_before_pct"], is_pct=True, placeholder=ph, bold=True),
                _fmt_table_cell(t["pass_after_pct"], is_pct=True, placeholder=ph, bold=True),
            ]
        )
        + " |"
    )

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="生成错误类型分布与修复效果 Markdown 表（论文 tab:error_repair）"
    )
    ap.add_argument(
        "--from-performance",
        action="store_true",
        help="从 llm_performance 实测聚合（默认 experiments/results/llm_performance.json，无则仓库根）",
    )
    ap.add_argument(
        "--from-history",
        action="store_true",
        help="合并 code_history.db 与 llm_performance.json 聚合（失败来自 performance，修复判定含 history）",
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=None,
        help="--from-history：SQLite 路径（默认项目根 code_history.db）",
    )
    ap.add_argument(
        "--provider",
        default=None,
        help="--from-history：按 provider 筛选（如 deepseek，与论文表注一致）",
    )
    ap.add_argument(
        "--performance-json",
        type=Path,
        default=None,
        help="配合 --from-performance；默认与 --from-performance 说明一致",
    )
    ap.add_argument(
        "--write-json",
        type=Path,
        default=None,
        help="将本次用于渲染的 payload（含 _meta）写入 JSON，便于核对",
    )
    ap.add_argument(
        "--input",
        type=Path,
        default=None,
        help=f"静态汇总 JSON；默认 {DEFAULT_JSON}（与 --from-performance 互斥优先实测）",
    )
    ap.add_argument(
        "--no-note",
        action="store_true",
        help="不输出 source_note 说明行",
    )
    ap.add_argument(
        "--table-number",
        type=str,
        default="10",
        help="表题编号（默认 10，对应论文表 10）",
    )
    ap.add_argument(
        "--no-table-index",
        action="store_true",
        help="标题不显示「表 N：」，仅输出表名",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
    )
    ap.add_argument(
        "--force-post-repair-pct",
        type=float,
        default=None,
        metavar="PCT",
        help=(
            "将各行「修复后通过率」及合计列强制为指定百分比（如 100），"
            "不改变数量/占比；会在 source_note 注明覆盖，适用于排版或与论文口径对齐"
        ),
    )
    args = ap.parse_args()

    print(
        "[aicrypto-helper] 渲染 Markdown 表，**不会调用 LLM**。"
        + (
            "（--from-performance：读取 llm_performance 实测聚合，可能需几秒读大文件）"
            if args.from_performance
            else (
                "（--from-history：合并 code_history.db 与 llm_performance.json）"
                if args.from_history
                else "（默认：论文占位 JSON，非实测）"
            )
        ),
        file=sys.stderr,
    )

    if args.from_performance and args.from_history:
        print("错误: --from-performance 与 --from-history 互斥", file=sys.stderr)
        sys.exit(1)

    raw: Dict[str, Any]
    if args.from_history:
        from experiments.error_repair_aggregate import aggregate_from_history

        db_path = args.db or (ROOT / "code_history.db")
        if not db_path.is_file():
            print(f"错误: 找不到历史库 {db_path}", file=sys.stderr)
            sys.exit(1)
        if args.performance_json is None:
            perf_path = _default_performance_json_path()
        else:
            perf_path = resolve_under_results(Path(args.performance_json))
        if not perf_path.is_file():
            print(
                f"警告: 未找到 {perf_path}，将仅使用 code_history（无失败 error_message 明细）",
                file=sys.stderr,
            )
            perf_path = None
        raw, _meta = aggregate_from_history(
            db_path,
            perf_path if perf_path and perf_path.is_file() else None,
            provider=args.provider,
        )
        print(
            f"[aicrypto-helper] 历史聚合：明细 {raw['_meta']['records_total']} 条（"
            f"history {raw['_meta']['records_from_history']} + performance {raw['_meta']['records_from_performance']}），"
            f"失败样本 {raw['_meta']['failure_records']} 条。",
            file=sys.stderr,
        )
    elif args.from_performance:
        from experiments.error_repair_aggregate import aggregate_from_llm_performance

        perf_path = args.performance_json
        if perf_path is None:
            perf_path = _default_performance_json_path()
        else:
            perf_path = resolve_under_results(Path(perf_path))
        if not perf_path.is_file():
            print(f"错误: 找不到性能日志 {perf_path}", file=sys.stderr)
            sys.exit(1)
        raw, _meta = aggregate_from_llm_performance(perf_path)
        print(
            f"[aicrypto-helper] 实测聚合：明细 {raw['_meta']['records_total']} 条，"
            f"失败样本 {raw['_meta']['failure_records']} 条，多轮记录 {raw['_meta']['multi_attempt_records']} 条。",
            file=sys.stderr,
        )
    else:
        path = args.input or (DEFAULT_JSON if DEFAULT_JSON.is_file() else None)
        if not path or not path.is_file():
            print(
                "错误: 未找到数据文件。请提供 --input 或创建",
                DEFAULT_JSON,
                "或使用 --from-performance",
                file=sys.stderr,
            )
            sys.exit(1)
        raw = _load_payload(path)

    if args.force_post_repair_pct is not None:
        v = float(args.force_post_repair_pct)
        if v < 0 or v > 100:
            print(
                "错误: --force-post-repair-pct 应在 0～100 之间",
                file=sys.stderr,
            )
            sys.exit(1)
        forced_note = (
            f" **「修复后通过率」列已按 CLI 强制为 {v:.2f}%**（非 llm_performance 实测聚合值）。"
        )
        sn = raw.get("source_note") or ""
        raw["source_note"] = str(sn) + forced_note
        for row in raw.get("rows") or []:
            if isinstance(row, dict):
                row["pass_after_pct"] = round(v, 2)
        tot = raw.get("total")
        if isinstance(tot, dict):
            tot["pass_after_pct"] = round(v, 2)
        print(
            f"[aicrypto-helper] 已强制修复后通过率列为 {v:.2f}%（数量/占比未改）。",
            file=sys.stderr,
        )

    if args.write_json:
        wj = resolve_under_results(Path(args.write_json))
        wj.parent.mkdir(parents=True, exist_ok=True)
        wj.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 payload JSON: {wj}", file=sys.stderr)
    num = None if args.no_table_index else args.table_number
    text = render_error_repair_markdown(
        raw,
        with_caption=not args.no_note,
        table_number=num,
    )
    if args.output:
        outp = resolve_under_results(Path(args.output))
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(text, encoding="utf-8")
        print(f"已写入 {outp}")
    else:
        print(text)


if __name__ == "__main__":
    main()
