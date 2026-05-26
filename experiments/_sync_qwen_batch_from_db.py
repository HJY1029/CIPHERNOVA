#!/usr/bin/env python3
"""根据 code_history 输出建议的 QWEN_FAILING / QWEN_FORCE_REGEN 槽位（打印可复制元组）。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments._slot_fail_analysis import analyze_slots, merged_records  # noqa: E402
from experiments.extract_llm_performance_from_history import build_reference_slot_keys  # noqa: E402
from utils.config_loader import ConfigLoader  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402

PROV = "qwen_coder_local"


def _tuple_sk(sk: tuple) -> tuple:
    a, m, lang = sk
    return (a, m.upper() if m else None, lang)


def main() -> None:
    cfg = ConfigLoader(str(ROOT / "config.yaml"))
    ref = build_reference_slot_keys(cfg)
    db = ROOT / "code_history.db"
    perf = ROOT / "experiments" / "results" / "llm_performance.json"
    merged = merged_records(PROV, db, perf)
    grid = analyze_slots(PROV, ref, merged)
    fail_labels = set(grid["fail_vpr"] + grid["fail_ftpr_only"])

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT algorithm, mode, language FROM code_history "
        "WHERE lower(provider)=lower(?) AND test_success=1",
        (PROV,),
    ).fetchall()
    conn.close()
    db_ok = {HistoryManager.normalize_case_key(a, m, l) for a, m, l in rows}

    failing: list = []
    force_no_db: list = []
    for sk in sorted(ref):
        a, m, lang = sk
        lab = f"{a}|{m or '-'}|{lang}"
        if lab in fail_labels:
            failing.append(_tuple_sk(sk))
        elif sk not in db_ok:
            force_no_db.append(_tuple_sk(sk))

    print("# 建议写入 scripts/qwen_batch_common.py")
    print("QWEN_FAILING_SLOT_KEYS = (")
    for t in failing:
        print(f'    ("{t[0]}", "{t[1]}", "{t[2]}"),')
    print(")")
    print("QWEN_FORCE_REGEN_NO_DB_KEYS = (")
    for t in force_no_db:
        mode = f'"{t[1]}"' if t[1] else "None"
        print(f'    ("{t[0]}", {mode}, "{t[2]}"),')
    print(")")
    print(f"# total={len(failing)+len(force_no_db)} fail={len(failing)} no_db={len(force_no_db)}")


if __name__ == "__main__":
    main()
