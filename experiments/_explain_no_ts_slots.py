#!/usr/bin/env python3
"""解释「无 DB 首次成功」槽位为何不在 QWEN_FAILING_SLOT_KEYS。"""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments._slot_fail_analysis import analyze_slots, merged_records  # noqa: E402
from experiments.extract_llm_performance_from_history import (  # noqa: E402
    _slot_key_record,
    build_reference_slot_keys,
)
from scripts.qwen_batch_common import QWEN_FAILING_SLOT_KEYS  # noqa: E402
from utils.config_loader import ConfigLoader  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402

USER_SLOTS = [
    ("AES", "CFB", "python"),
    ("AES", "CTR", "cpp"),
    ("AES", "CTR", "python"),
    ("AES", "GCM", "cpp"),
    ("AES", "GCM", "python"),
    ("RSA", None, "cpp"),
    ("RSA", None, "python"),
    ("SM4", "ECB", "python"),
    ("SM4", "OFB", "cpp"),
    ("SM4", "OFB", "python"),
]


def main() -> None:
    cfg = ConfigLoader(str(ROOT / "config.yaml"))
    ref = build_reference_slot_keys(cfg)
    db = ROOT / "code_history.db"
    perf = ROOT / "experiments" / "results" / "llm_performance.json"
    merged = merged_records("qwen_coder_local", db, perf)
    grid = analyze_slots("qwen_coder_local", ref, merged)
    pass_set = set(grid["pass"])
    fail_set = set(grid["fail_vpr"] + grid["fail_ftpr_only"])
    failing_keys = {
        HistoryManager.normalize_case_key(a, m, l)
        for a, m, l in QWEN_FAILING_SLOT_KEYS
    }
    by = defaultdict(list)
    for r in merged:
        by[_slot_key_record(r)].append(r)

    print(f"QWEN_FAILING_SLOT_KEYS ({len(QWEN_FAILING_SLOT_KEYS)}):")
    for a, m, l in QWEN_FAILING_SLOT_KEYS:
        print(f"  {a}|{m or '-'}|{l}")
    print()

    for alg, mode, lang in USER_SLOTS:
        sk = HistoryManager.normalize_case_key(alg, mode, lang)
        # 与 analyze_slots / _slot_fail_analysis 一致：mode 小写
        m_key = (mode or "").strip().lower() if mode else "-"
        lab = f"{alg}|{m_key if m_key else '-'}|{lang}"
        rs = by.get(sk, [])
        t_ok = any(x.get("test_success") in (True, 1) for x in rs)
        v_ok = any(x.get("validation_success") is True for x in rs)
        db_ok = [
            r
            for r in rs
            if r.get("test_success") in (True, 1) and (r.get("timestamp") or "").strip()
        ]
        json_only = [
            r
            for r in rs
            if r.get("test_success") in (True, 1) and not (r.get("timestamp") or "").strip()
        ]
        in_failing = sk in failing_keys
        if lab in pass_set:
            status = "网格判定=通过"
        elif lab in fail_set:
            status = "网格判定=未过"
        else:
            status = "网格判定=其它/无记录"
        why = []
        if t_ok and not db_ok:
            why.append("通过来自 llm_performance.json（无 timestamp）")
        if db_ok:
            why.append(f"DB 有成功时间 {min(r['timestamp'] for r in db_ok)[:19]}")
        if in_failing:
            why.append("已在 QWEN_FAILING_SLOT_KEYS")
        elif t_ok:
            why.append("已过→不应进失败批量列表")
        elif lab in fail_set:
            why.append("未过但未在固定失败列表（需手动同步列表）")
        print(f"{lab}")
        print(f"  {status}; failing_list={in_failing}; json_only_pass={len(json_only)}")
        print(f"  → {'; '.join(why)}")


if __name__ == "__main__":
    main()
