#!/usr/bin/env python3
"""按时间划分 qwen 各槽首次 test_success=1 相对蒸馏启用分界。"""
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments._slot_fail_analysis import merged_records  # noqa: E402
from experiments.extract_llm_performance_from_history import (  # noqa: E402
    _slot_key_record,
    build_reference_slot_keys,
)
from utils.config_loader import ConfigLoader  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402


def teacher_pool_first_ts() -> str:
    p = ROOT / "data" / "distillation_teacher.jsonl"
    first = None
    if not p.is_file():
        return "2026-05-07T00:00:00"
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        ts = o.get("ts") or o.get("recorded_at")
        if ts and (first is None or ts < first):
            first = ts
    return first or "2026-05-07T00:00:00"


def main() -> None:
    cfg = ConfigLoader(str(ROOT / "config.yaml"))
    ref = build_reference_slot_keys(cfg)
    ref_labels = sorted(
        f"{a}|{(m or '-').upper() if m else '-'}|{lang}"
        for a, m, lang in ref
    )

    cutoff = teacher_pool_first_ts()
    print(f"分界时间（教师池首条 ts，≈蒸馏可注入起点）: {cutoff}")
    print()

    prov = "qwen_coder_local"
    db = ROOT / "code_history.db"
    perf = ROOT / "experiments" / "results" / "llm_performance.json"
    if not perf.is_file():
        perf = ROOT / "llm_performance.json"
    merged = merged_records(prov, db, perf)

    by_slot: dict = defaultdict(list)
    for r in merged:
        by_slot[_slot_key_record(r)].append(r)

    before: list[tuple[str, str, str]] = []
    after: list[tuple[str, str, str, str]] = []
    no_success: list[str] = []
    has_distill_flag: list[tuple[str, str]] = []

    for sk in sorted(ref):
        a, m, lang = sk
        label = f"{a}|{m.upper() if m else '-'}|{lang}"
        recs = by_slot.get(sk, [])
        first_ok_ts = None
        first_ok_distill = None
        passing = [x for x in recs if x.get("test_success") in (True, 1)]
        for r in passing:
            ts = (r.get("timestamp") or "").strip()
            if ts and (first_ok_ts is None or ts < first_ok_ts):
                first_ok_ts = ts
            ex = r.get("extra_data")
            if ex:
                try:
                    ed = json.loads(ex) if isinstance(ex, str) else ex
                except json.JSONDecodeError:
                    ed = {}
                if isinstance(ed, dict) and "distillation_enabled" in ed:
                    has_distill_flag.append(
                        (label, str(ed.get("distillation_enabled")))
                    )
                    if first_ok_distill is None:
                        first_ok_distill = (
                            ts,
                            bool(ed.get("distillation_enabled")),
                        )
        currently_pass = bool(passing)
        if not currently_pass:
            no_success.append(label)
        elif first_ok_ts is None:
            no_success.append(label + " (pass无timestamp)")
        elif first_ok_ts < cutoff:
            before.append((label, first_ok_ts))
        else:
            extra = ""
            if first_ok_distill:
                extra = f" distill_flag={first_ok_distill[1]}"
            after.append((label, first_ok_ts, extra))

    pass_now = len(before) + len(after)
    print(f"参考网格 {len(ref)} 槽；合并记录覆盖 {len(by_slot)} 槽")
    print(f"当前合并口径通过且能定位首次成功时间: {pass_now}")
    print()
    print(f"=== 首次通过 < 分界（视为蒸馏机制生效前已通过）: {len(before)} ===")
    for label, ts in before:
        print(f"  {label}  @ {ts}")
    print()
    print(f"=== 首次通过 >= 分界（视为蒸馏可用后首次通过）: {len(after)} ===")
    for label, ts, extra in after:
        print(f"  {label}  @ {ts}{extra}")
    print()
    print(f"=== 网格内仍无 qwen 首次成功记录: {len(no_success)} ===")
    for x in no_success:
        print(f"  {x}")

    if has_distill_flag:
        print()
        print(f"extra_data 含 distillation_enabled 的成功相关记录: {len(has_distill_flag)}")
        on = sum(1 for _, v in has_distill_flag if v == "True")
        off = sum(1 for _, v in has_distill_flag if v == "False")
        print(f"  True={on} False={off}")
    else:
        print()
        print("注: 历史记录 extra_data 尚无 distillation_enabled 字段，时间分界仅按教师池首条 ts。")


def main_db_authoritative() -> None:
    """仅用 code_history.db 中 qwen 行的最早成功时间（不受 JSON 无 timestamp 干扰）。"""
    cfg = ConfigLoader(str(ROOT / "config.yaml"))
    ref = build_reference_slot_keys(cfg)
    cutoff = teacher_pool_first_ts()
    db = ROOT / "code_history.db"
    perf = ROOT / "experiments" / "results" / "llm_performance.json"
    if not perf.is_file():
        perf = ROOT / "llm_performance.json"
    from experiments._slot_fail_analysis import analyze_slots  # noqa: E402

    merged = merged_records("qwen_coder_local", db, perf)
    pass_set = set(analyze_slots("qwen_coder_local", ref, merged)["pass"])

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM code_history WHERE lower(provider)=lower(?) ORDER BY timestamp",
        ("qwen_coder_local",),
    ).fetchall()
    conn.close()
    by_slot: dict = defaultdict(list)
    for r in rows:
        sk = HistoryManager.normalize_case_key(
            r["algorithm"], r["mode"], r["language"]
        )
        by_slot[sk].append(dict(r))

    def label(sk: tuple) -> str:
        a, m, lang = sk
        return f"{a}|{m or '-'}|{lang}"

    before: list[tuple[str, str, bool]] = []
    after: list[tuple[str, str, bool]] = []
    only_json: list[str] = []
    for sk in sorted(ref):
        lab = label(sk)
        first = None
        for r in by_slot.get(sk, []):
            if r.get("test_success"):
                ts = r["timestamp"]
                if first is None or ts < first:
                    first = ts
        still = lab in pass_set
        if first:
            (before if first < cutoff else after).append((lab, first, still))
        elif still:
            only_json.append(lab)

    print("--- DB 权威时间轴（provider=qwen_coder_local）---")
    print(f"分界: {cutoff}")
    print(f"当前合并口径通过: {len(pass_set)}/45")
    print(f"蒸馏前首次成功(DB): {len(before)}")
    for lab, ts, still in before:
        print(f"  {lab}  {ts}  {'仍过' if still else '已不过'}")
    print(f"蒸馏后首次成功(DB): {len(after)}")
    for lab, ts, still in after:
        print(f"  {lab}  {ts}")
    print(f"仅 llm_performance 标记通过、DB 无成功: {len(only_json)}")
    for lab in only_json:
        print(f"  {lab}")


if __name__ == "__main__":
    main_db_authoritative()
    print()
    main()
