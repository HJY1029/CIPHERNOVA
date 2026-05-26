#!/usr/bin/env python3
"""按 45 槽（config_grid 口径）列出 provider 未通过格子。"""
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments.extract_llm_performance_from_history import (  # noqa: E402
    _effective_records_for_provider,
    _group_records_by_provider,
    _merge_llm_performance_by_provider,
    _slot_key_record,
    build_reference_slot_keys,
)
from utils.config_loader import ConfigLoader  # noqa: E402


def merged_records(provider: str, db_path: Path, perf_path: Path) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    by_prov = _group_records_by_provider(conn)
    conn.close()
    bucket = None
    if perf_path.is_file():
        raw = json.loads(perf_path.read_text(encoding="utf-8"))
        bucket = _merge_llm_performance_by_provider(raw).get(provider)
    return _effective_records_for_provider(bucket, by_prov.get(provider, []))


def analyze_slots(
    provider: str, ref: Set[Tuple[str, str, str]], records: List[Dict[str, Any]]
) -> Dict[str, List[str]]:
    slots: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        slots[_slot_key_record(r)].append(r)

    def label(sk: Tuple[str, str, str]) -> str:
        return f"{sk[0]}|{sk[1] or '-'}|{sk[2]}"

    out = {
        "no_record": [],
        "fail_vpr": [],
        "fail_ftpr_only": [],
        "pass": [],
    }
    for sk in sorted(ref):
        rs = slots.get(sk, [])
        if not rs:
            out["no_record"].append(label(sk))
            continue
        v_ok = any(x.get("validation_success") is True for x in rs)
        t_ok = any(x.get("test_success") is True for x in rs)
        if t_ok:
            out["pass"].append(label(sk))
        elif v_ok:
            out["fail_ftpr_only"].append(label(sk))
        else:
            out["fail_vpr"].append(label(sk))
    return out


def main() -> None:
    cfg = ConfigLoader(str(ROOT / "config.yaml"))
    ref = build_reference_slot_keys(cfg)
    db = ROOT / "code_history.db"
    perf = ROOT / "experiments" / "results" / "llm_performance.json"
    if not perf.is_file():
        perf = ROOT / "llm_performance.json"
    for p in ("doubao", "qwen_coder_local"):
        recs = merged_records(p, db, perf)
        b = analyze_slots(p, ref, recs)
        n = len(ref)
        print(f"=== {p} (records={len(recs)}, grid={n}) ===")
        print(
            f"  pass={len(b['pass'])} no_record={len(b['no_record'])} "
            f"vpr_fail={len(b['fail_vpr'])} ftpr_only_fail={len(b['fail_ftpr_only'])}"
        )
        for key in ("no_record", "fail_vpr", "fail_ftpr_only"):
            if b[key]:
                print(f"  -- {key} --")
                for x in b[key]:
                    print(f"    {x}")


if __name__ == "__main__":
    main()
