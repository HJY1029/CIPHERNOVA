#!/usr/bin/env python3
"""对照 code_history + llm_performance，列出 QWEN_FAILING_SLOT_KEYS 中已 test_success 的槽。"""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments._slot_fail_analysis import merged_records  # noqa: E402
from experiments.extract_llm_performance_from_history import _slot_key_record  # noqa: E402
from scripts.qwen_batch_common import (  # noqa: E402
    QWEN_FAILING_SLOT_KEYS,
    QWEN_FORCE_REGEN_NO_DB_KEYS,
    _all_qwen_batch_slot_keys,
)
from utils.history_manager import HistoryManager  # noqa: E402

PROVIDER = "qwen_coder_local"


def slot_key(alg: str, mode: str, lang: str):
    return HistoryManager.normalize_case_key(alg, mode, lang)


def main() -> None:
    db = ROOT / "code_history.db"
    perf = ROOT / "experiments" / "results" / "llm_performance.json"
    if not perf.is_file():
        perf = ROOT / "llm_performance.json"
    recs = merged_records(PROVIDER, db, perf)
    slots: dict = defaultdict(list)
    for r in recs:
        try:
            slots[_slot_key_record(r)].append(r)
        except Exception:
            pass

    passed: list = []
    still_fail: list = []
    for alg, mode, lang in _all_qwen_batch_slot_keys():
        sk = slot_key(alg, mode, lang)
        rs = slots.get(sk, [])
        t_ok = any(x.get("test_success") is True for x in rs)
        v_ok = any(x.get("validation_success") is True for x in rs)
        lab = f"{alg}|{mode}|{lang}"
        if t_ok:
            passed.append((alg, mode, lang, lab))
        else:
            st = "no_record" if not rs else ("ftpr" if v_ok else "vpr")
            still_fail.append((alg, mode, lang, lab, st))

    print(f"provider={PROVIDER} records={len(recs)}")
    print(
        f"batch_slots={len(_all_qwen_batch_slot_keys())} "
        f"(fail={len(QWEN_FAILING_SLOT_KEYS)} force_no_db={len(QWEN_FORCE_REGEN_NO_DB_KEYS)})"
    )
    print(f"passed_in_list={len(passed)} still_fail={len(still_fail)}")
    print("\n-- PASSED (remove from list) --")
    for _, _, _, lab in passed:
        print(lab)
    print("\n-- STILL_FAIL (keep) --")
    for _, _, _, lab, st in still_fail:
        print(f"{lab} ({st})")


if __name__ == "__main__":
    main()
