"""论文消融脚本共用：从 code_history SQLite 回填「完整方法」格子，省去 LLM API。

对齐 Web 入库字段：algorithm / provider / mode / language / validation_success / test_success / code / filename。
每格取 ``ORDER BY datetime(timestamp) DESC, id DESC LIMIT 1`` 最新一条。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional


def fetch_latest_cell_metrics(
    db_path: Path,
    *,
    algorithm: str,
    provider: str,
    mode: str,
    language: str,
) -> Optional[Dict[str, Any]]:
    """若无匹配行返回 ``None``；否则返回 gsr/vpr/ftpr 及元数据。"""
    if not db_path.is_file():
        return None
    algo_u = (algorithm or "").strip().upper() or "DES"
    prov_q = (provider or "").strip()
    mode_q = (mode or "").strip()
    lang_q = (language or "").strip().lower()

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT code, validation_success, test_success, filename, timestamp
            FROM code_history
            WHERE UPPER(TRIM(algorithm)) = ?
              AND LOWER(TRIM(provider)) = LOWER(TRIM(?))
              AND TRIM(mode) = ?
              AND LOWER(TRIM(language)) = ?
            ORDER BY datetime(timestamp) DESC, id DESC
            LIMIT 1
            """,
            (algo_u, prov_q, mode_q, lang_q),
        )
        row = cur.fetchone()
        if row is None:
            return None
        code = row["code"] or ""
        gsr = len(str(code).strip()) > 30
        vs = row["validation_success"]
        vpr = vs in (1, True)
        ts = row["test_success"]
        if ts is None:
            ftpr_b: Optional[bool] = None
        else:
            ftpr_b = bool(ts)
        ftpr = bool(ftpr_b) if ftpr_b is not None else False
        return {
            "gsr": gsr,
            "vpr": vpr,
            "ftpr": ftpr if ftpr_b is not None else False,
            "test_success_recorded": ftpr_b is not None,
            "code_chars": len(str(code).strip()),
            "filename": row["filename"] or "",
            "history_timestamp": row["timestamp"],
        }
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def paper_variant_row_from_history(
    db_path: Path,
    *,
    case: Dict[str, Any],
    algorithm: str = "DES",
) -> Optional[Dict[str, Any]]:
    """供 ``run_paper_ablation`` 的扁平任务行拼装（结构与 ``_run_one_agent`` 成功返回接近）。"""
    t = fetch_latest_cell_metrics(
        db_path,
        algorithm=algorithm,
        provider=str(case.get("provider", "")),
        mode=str(case.get("mode", "")),
        language=str(case.get("language", "")),
    )
    if t is None:
        return None
    fn = (t.get("filename") or "").strip()
    return {
        "ok": True,
        "filepath": fn or "(history)",
        "code_chars": int(t.get("code_chars") or 0),
        "gsr": bool(t["gsr"]),
        "vpr": bool(t["vpr"]),
        "ftpr": bool(t["ftpr"]),
        "validation_hint": None,
        "test_hint": None,
        "_from_code_history": True,
        "_history_timestamp": t.get("history_timestamp"),
    }
