#!/usr/bin/env python3
"""列出 qwen：无 provider DB 成功 / 未过 / 批量表槽。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments._slot_fail_analysis import analyze_slots, merged_records  # noqa: E402
from experiments.extract_llm_performance_from_history import build_reference_slot_keys  # noqa: E402
from scripts.qwen_batch_common import (  # noqa: E402
    QWEN_FAILING_SLOT_KEYS,
    QWEN_FORCE_REGEN_NO_DB_KEYS,
    _all_qwen_batch_slot_keys,
)
from utils.config_loader import ConfigLoader  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402

PROV = "qwen_coder_local"


def main() -> None:
    cfg = ConfigLoader(str(ROOT / "config.yaml"))
    ref = build_reference_slot_keys(cfg)
    db = ROOT / "code_history.db"
    perf = ROOT / "experiments" / "results" / "llm_performance.json"
    merged = merged_records(PROV, db, perf)
    grid = analyze_slots(PROV, ref, merged)
    pass_set = set(grid["pass"])
    fail_set = set(grid["fail_vpr"] + grid["fail_ftpr_only"])

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT algorithm, mode, language FROM code_history "
        "WHERE lower(provider)=lower(?) AND test_success=1",
        (PROV,),
    ).fetchall()
    conn.close()
    db_ok = {HistoryManager.normalize_case_key(a, m, l) for a, m, l in rows}

    batch = {
        HistoryManager.normalize_case_key(a, m, l)
        for a, m, l in _all_qwen_batch_slot_keys()
    }

    def lab(sk):
        a, m, lang = sk
        return f"{a}|{m or '-'}|{lang}"

    need_work = []
    for sk in sorted(ref):
        l = lab(sk)
        has_db = sk in db_ok
        in_pass = l in pass_set
        in_fail = l in fail_set
        if not has_db or in_fail:
            need_work.append((l, has_db, in_pass, in_fail, sk in batch))

    print(f"grid=45 db_ok={len(db_ok)} pass={len(pass_set)} fail={len(fail_set)}")
    print(f"need_prompt_work (no_db OR fail): {len(need_work)}")
    print("\n=== 列表 (no_db | fail | json_pass | in_batch) ===")
    for l, has_db, jp, jf, ib in need_work:
        tags = []
        if not has_db:
            tags.append("NO_DB")
        if jf:
            tags.append("FAIL")
        if jp:
            tags.append("json_pass")
        if ib:
            tags.append("batch")
        print(f"  {l}  {'/'.join(tags)}")

    print(f"\nFAILING={len(QWEN_FAILING_SLOT_KEYS)} FORCE_NO_DB={len(QWEN_FORCE_REGEN_NO_DB_KEYS)}")


if __name__ == "__main__":
    main()
