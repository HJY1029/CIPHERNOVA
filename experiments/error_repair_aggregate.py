"""
从 llm_performance.json 聚合「错误类型 × 数量 × 多轮修复通过率」（tab:error_repair 数据源）。

**实验总体（与论文「四类错误」表一致）：**
- **只收集失败样本**：`validation_success===false` 或 `test_success===false` 的性能记录；**成功记录不参与**分类计数与通过率分母。
- **每条失败必归入四类之一**（`classify_record` 启发式；无第五类，未命中关键词时进「实现细节」）。
- **四类各自**：该类的失败条数、**修复前通过率**（本条即 `test_success` 的比例）、**修复后/已跑通比例**（本条或同 case 更晚出现向量通过，定义见下）。
- **合计行**：分母 = 全部失败条数（= 四类数量之和）。

**重要（避免与「分类型自动修复」混淆）：**
- 本模块**不做**按四类分别调用不同修复策略；**不**依赖 ``chase_repair_to_completion.py`` 参与公式。
- 「修复后通过率」是 **日志观测口径**：对每条**失败**记录，若本条已 `test_success===true`，或 **同一 model_key + (算法,模式,语言)** 在**更晚**的 `llm_performance` 记录里出现 `test_success===true`，则计为「已跑通」。反映的是 *同一任务线上最终是否成功*，不是「对该 error_message 执行了某类修复动作」。
- ``chase_repair_to_completion`` 等补跑脚本只是**额外产生**更晚的成功记录，从而提高上述比例；表也可完全由 Web/批量自然重试生成，无需该脚本。

说明：
- 「数量」统计 **validation 失败或 test 失败** 的记录条数（与 CryptoAgent._record_performance 写入口径一致）。
- 「修复后通过率」按 **失败样本**（与「数量」同一批）逐类统计：该条本条已 `test_success===true`（少数校验失败但测试标真等边界），或 **同一模型 + 同一 (算法,模式,语言)** 在 **时间更晚** 的日志里出现过 `test_success===true`，则该失败行计为「后续已跑通」（含仅 `attempts==1` 的失败 + Web 再次点击生成后成功）。
- 「修复前通过率」：表内「数量」均为**失败样本**（校验失败或测试失败）。对该子集逐条看本条日志的 ``test_success``：**已通过向量测试**的比例（分母=该类失败条数）；多为 **0%**（测试未通过），少数为校验失败但测试已通过等边界。非「全库首轮成功率」估计（JSON 无首轮字段）。
- 分类为基于 error_message 关键词的启发式，与论文人工标注 159 例不完全等价；论文静态表见 experiments/data/error_repair_table.json。
- **链式模式密文不一致**：OFB/CFB/CTR/GCM 下「密文/向量不匹配」优先归入「算法理解」（模式语义错误），不再一律落入「实现细节」。
- **首次生成推断失败**：从同 case 时间线补全最近一条失败 `error_message`（``_error_message_enriched``），无文本时链式模式弱启发归入「算法理解」。
- **`aggregate_from_history`**：合并 `code_history.db`（Web 成功落库）与 `llm_performance.json`（失败明细）；修复判定时间线按 **provider×算法×模式×语言** 对齐，history 中更晚成功可计为已修复。
- **首次生成内修复**：对 ``attempts>1`` 且最终 ``test_success===true`` 的成功记录（history 或 performance），按 ``(provider,算法,模式,语言)+timestamp(秒)`` 去重后，各推断 **1 条** 首次生成中间失败样本（``_first_gen_repaired``），计入分类数量且修复后通过率计为已跑通；反映 Self-Refine / 生成重试在同一次点击生成内 eventual success，而非仅「再次点击生成后成功」。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CAT_ORDER = ["算法理解", "实现细节", "环境配置", "代码结构"]


def flatten_performance_records(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """合并所有 provider:model 桶下的 records。"""
    out: List[Dict[str, Any]] = []
    uid = 0
    for mk, bucket in data.items():
        if not isinstance(bucket, dict):
            continue
        for r in bucket.get("records") or []:
            if isinstance(r, dict):
                row = dict(r)
                row["_model_key"] = mk
                row["_flat_uid"] = uid
                uid += 1
                out.append(row)
    return out


def _normalize_provider_slug(r: Dict[str, Any]) -> str:
    prov = (r.get("provider") or "").strip()
    if prov:
        return prov
    mk = str(r.get("_model_key") or "")
    if ":" in mk:
        return mk.split(":", 1)[0].strip()
    return mk.strip() or "unknown"


def _case_key(r: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """与单次生成任务对齐的键（同线路、同算法/模式/语言）。"""
    lane = _normalize_provider_slug(r) if r.get("_case_by_provider") else str(r.get("_model_key") or "")
    return (
        lane,
        (r.get("algorithm") or "").strip().upper(),
        (r.get("mode") or "").strip().lower(),
        (r.get("language") or "").strip().lower(),
    )


def _build_case_lists_sorted(flat: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str, str], List[Dict[str, Any]]]:
    by_case: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    for r in flat:
        by_case.setdefault(_case_key(r), []).append(r)
    sk = lambda x: (x.get("timestamp") or "", int(x.get("_flat_uid", 0)))
    for lst in by_case.values():
        lst.sort(key=sk)
    return by_case


def _repair_resolved_after(
    r: Dict[str, Any],
    by_case: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]],
    final_test_pass,
) -> bool:
    """本条已通过，或同 case 在之后存在一条 test_success===true 的日志（视为后续跑通）。"""
    if r.get("_first_gen_repaired"):
        return True
    if final_test_pass(r):
        return True
    lst = by_case.get(_case_key(r)) or []
    uid_r = int(r.get("_flat_uid", -1))
    idx = -1
    for i, x in enumerate(lst):
        if int(x.get("_flat_uid", -2)) == uid_r:
            idx = i
            break
    if idx < 0:
        return False
    for j in range(idx + 1, len(lst)):
        if final_test_pass(lst[j]):
            return True
    return False


def _is_failure_record(r: Dict[str, Any]) -> bool:
    if r.get("validation_success") is False:
        return True
    if r.get("test_success") is False:
        return True
    return False


def _is_chain_mode(r: Dict[str, Any]) -> bool:
    mode = (r.get("mode") or "").upper()
    return any(x in mode for x in ("OFB", "CFB", "CTR", "GCM"))


def classify_from_message(msg_l: str, r: Dict[str, Any]) -> str:
    """根据错误文本与任务上下文归入四类之一。"""
    algorithm = (r.get("algorithm") or "").upper()
    # 环境 / 依赖 / 变量名
    env_kw = (
        "openssl/evp.h",
        "no such file or directory",
        "cannot find -l",
        "legacy provider",
        "module named",
        "modulenotfound",
        "importerror",
        "test_key",
        "getenv",
        "环境变量",
        "未安装",
        "开发库",
        "dll",
        "cannot open shared object",
    )
    if any(k in msg_l for k in env_kw):
        return "环境配置"

    struct_kw = (
        "fatal error:",
        "undefined reference",
        "was not declared",
        "expected ",
        "syntax error",
        "compilation terminated",
        "undefined symbol",
        "collect2:",
        "linker",
        "redeclared",
        "extra tokens at end of #include",
    )
    if any(k in msg_l for k in struct_kw):
        return "代码结构"

    # 算法 / 模式 / 密钥尺度混淆
    algo_kw = (
        "des密钥",
        "aes密钥",
        "密钥必须为",
        "密钥长度",
        "wrong mode",
        "ofb",
        "cfb",
        "ecb",
        "cbc",
        "padding",
        "block size",
        "模式",
        "segment_size",
    )
    if any(k in msg_l for k in algo_kw):
        return "算法理解"
    if algorithm == "AES" and "des" in msg_l:
        return "算法理解"

    # 链式/流式模式下的向量密文不一致，根因多为模式/填充/反馈语义理解错误
    output_mismatch_kw = (
        "密文",
        "ciphertext",
        "不匹配",
        "expected ciphertext",
        "预期",
        "实际结果",
        "实际密文",
        "向量",
    )
    if _is_chain_mode(r) and any(k in msg_l for k in output_mismatch_kw):
        return "算法理解"

    impl_kw = (
        "密文",
        "ciphertext",
        "不匹配",
        "向量",
        "expected ciphertext",
        "iv",
        "hex",
        "长度",
        "byte",
        "openssl比较",
    )
    if any(k in msg_l for k in impl_kw):
        return "实现细节"

    return "实现细节"


def classify_record(r: Dict[str, Any]) -> str:
    """单条记录分类（失败样本与 multi-attempt 样本共用）。"""
    msg = (r.get("error_message") or "").strip()
    if msg:
        return classify_from_message(msg.lower(), r)
    # 无错误文本：多轮成功等情形，用任务模式弱启发
    if r.get("test_success") is True:
        if _is_chain_mode(r):
            return "算法理解"
        return "实现细节"
    if r.get("_first_gen_repaired") and _is_chain_mode(r):
        return "算法理解"
    return "实现细节"


def four_category_failure_experiment_stats(flat: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    「四类错误」实验核心统计：**仅失败样本**；每条失败归入 ``CAT_ORDER`` 之一。

    返回 dict 含：``counts``, ``repair_pass_before``, ``repair_pass_after``（按类），
    ``fail_total``, ``failure_resolved_n``, ``overall_before``, ``overall_after``（合计，分母=全部失败条数）。
    """
    by_case_sorted = _build_case_lists_sorted(flat)

    def _final_test_pass(r: Dict[str, Any]) -> bool:
        return r.get("test_success") is True

    def _resolved(r: Dict[str, Any]) -> bool:
        return _repair_resolved_after(r, by_case_sorted, _final_test_pass)

    failures = [r for r in flat if _is_failure_record(r)]
    fail_total = len(failures)
    counts: Dict[str, int] = {k: 0 for k in CAT_ORDER}
    for r in failures:
        counts[classify_record(r)] += 1

    repair_pass_after: Dict[str, Optional[float]] = {}
    repair_pass_before: Dict[str, Optional[float]] = {}
    for cat in CAT_ORDER:
        fail_cat = [r for r in failures if classify_record(r) == cat]
        if not fail_cat:
            repair_pass_after[cat] = None
            repair_pass_before[cat] = None
        else:
            ok = sum(1 for r in fail_cat if _resolved(r))
            repair_pass_after[cat] = round(100.0 * ok / len(fail_cat), 2)
            pb = sum(1 for r in fail_cat if r.get("test_success") is True)
            repair_pass_before[cat] = round(100.0 * pb / len(fail_cat), 2)

    failure_resolved_n = sum(1 for r in failures if _resolved(r))
    if not failures:
        overall_after: Optional[float] = None
        overall_before: Optional[float] = None
    else:
        overall_after = round(100.0 * failure_resolved_n / len(failures), 2)
        fb = sum(1 for r in failures if r.get("test_success") is True)
        overall_before = round(100.0 * fb / len(failures), 2)

    return {
        "counts": counts,
        "repair_pass_before": repair_pass_before,
        "repair_pass_after": repair_pass_after,
        "fail_total": fail_total,
        "failure_resolved_n": failure_resolved_n,
        "overall_before": overall_before,
        "overall_after": overall_after,
    }


def flatten_code_history_records(
    db_path: Path,
    *,
    provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """将 code_history 行转为与 llm_performance records 兼容的 flat 结构（成功落库为主）。"""
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT timestamp, algorithm, mode, language, provider,
               validation_success, test_success, attempts, generation_time, extra_data
        FROM code_history
    """
    params: List[Any] = []
    if provider:
        sql += " WHERE provider = ?"
        params.append(provider.strip())
    sql += " ORDER BY timestamp ASC"
    cur = conn.execute(sql, params)
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        error_message: Optional[str] = None
        extra_raw = row["extra_data"]
        if extra_raw:
            try:
                extra = json.loads(extra_raw)
                if isinstance(extra, dict):
                    em = extra.get("error_message") or extra.get("last_error")
                    if em:
                        error_message = str(em)
            except (json.JSONDecodeError, TypeError):
                pass
        ts = row["test_success"]
        if ts is None:
            ts_out: Optional[bool] = None
        else:
            ts_out = bool(ts)
        vs = row["validation_success"]
        v_out = True if vs else False
        prov = (row["provider"] or "").strip() or "unknown"
        out.append(
            {
                "timestamp": row["timestamp"],
                "algorithm": row["algorithm"],
                "mode": row["mode"],
                "language": row["language"],
                "provider": prov,
                "validation_success": v_out,
                "test_success": ts_out,
                "attempts": int(row["attempts"] or 1),
                "generation_time": float(row["generation_time"] or 0.0),
                "error_message": error_message,
                "_source": "code_history",
                "_model_key": prov,
                "_case_by_provider": True,
            }
        )
    conn.close()
    return out


def _timestamp_bucket(ts: Optional[str]) -> str:
    """用于 history/performance 同次落库去重的秒级时间戳前缀。"""
    if not ts:
        return ""
    return str(ts).strip()[:19]


def _enrich_inferred_failure_messages(flat: List[Dict[str, Any]]) -> int:
    """
    为首次生成推断失败样本补全同 case 时间线上最近一条失败记录的 error_message，
    避免无文本时一律落入「实现细节」。
    """
    by_case = _build_case_lists_sorted(flat)
    enriched = 0
    for r in flat:
        if not r.get("_first_gen_repaired"):
            continue
        if (r.get("error_message") or "").strip():
            continue
        lst = by_case.get(_case_key(r)) or []
        ts_success = r.get("timestamp") or ""
        best_msg: Optional[str] = None
        best_key: Tuple[str, int] = ("", -1)
        for x in lst:
            if x is r or x.get("_first_gen_repaired"):
                continue
            if not _is_failure_record(x):
                continue
            msg = (x.get("error_message") or "").strip()
            if not msg:
                continue
            xts = x.get("timestamp") or ""
            if xts <= ts_success or _timestamp_bucket(xts) == _timestamp_bucket(ts_success):
                key = (xts, int(x.get("_flat_uid", 0)))
                if key > best_key:
                    best_key = key
                    best_msg = msg
        if best_msg:
            r["error_message"] = best_msg
            r["_error_message_enriched"] = True
            enriched += 1
    return enriched


def infer_first_generation_repair_failures(
    flat: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    从 attempts>1 的最终成功记录推断「首次生成内曾失败、后经 Self-Refine/重试修复」的样本。

    每条去重后的成功会话生成 1 条合成失败行（``_first_gen_repaired=True``），
    避免与 llm_performance 已记录的终态失败重复计数。
    """
    seen_success: set = set()
    synthetics: List[Dict[str, Any]] = []
    uid = max((int(r.get("_flat_uid", 0)) for r in flat), default=-1) + 1

    for r in flat:
        if r.get("test_success") is not True:
            continue
        att = int(r.get("attempts") or 1)
        if att <= 1:
            continue
        ck = _case_key(r)
        bucket = _timestamp_bucket(r.get("timestamp"))
        dedupe = (ck, bucket)
        if dedupe in seen_success:
            continue
        seen_success.add(dedupe)

        parent_msg = (r.get("error_message") or "").strip()
        synthetics.append(
            {
                "timestamp": r.get("timestamp"),
                "algorithm": r.get("algorithm"),
                "mode": r.get("mode"),
                "language": r.get("language"),
                "provider": _normalize_provider_slug(r),
                "validation_success": True,
                "test_success": False,
                "attempts": max(1, att - 1),
                "generation_time": float(r.get("generation_time") or 0.0),
                "error_message": parent_msg,
                "_source": "first_gen_inferred",
                "_first_gen_repaired": True,
                "_infer_from_attempts": att,
                "_infer_from_source": r.get("_source"),
                "_model_key": r.get("_model_key") or _normalize_provider_slug(r),
                "_case_by_provider": bool(r.get("_case_by_provider")),
                "_flat_uid": uid,
            }
        )
        uid += 1

    meta = {
        "first_gen_success_sessions": len(seen_success),
        "first_gen_inferred_failures": len(synthetics),
    }
    return synthetics, meta


def merge_history_and_performance_flat(
    *,
    db_path: Path,
    perf_path: Optional[Path] = None,
    provider: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    合并 code_history（Web 落库成功）与 llm_performance.json（含失败明细）。
    失败样本仅来自 performance 日志；修复判定时间线含 history 中更晚的成功记录。
    """
    hist = flatten_code_history_records(db_path, provider=provider)
    perf_flat: List[Dict[str, Any]] = []
    perf_mtime: Optional[str] = None
    if perf_path and perf_path.is_file():
        data = json.loads(perf_path.read_text(encoding="utf-8"))
        perf_flat = flatten_performance_records(data)
        perf_mtime = datetime.fromtimestamp(perf_path.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if provider:
            want = provider.strip()
            perf_flat = [r for r in perf_flat if _normalize_provider_slug(r) == want]
        for r in perf_flat:
            r["_case_by_provider"] = True
            r["_model_key"] = _normalize_provider_slug(r)
            r.setdefault("_source", "llm_performance")

    flat: List[Dict[str, Any]] = []
    uid = 0
    for r in hist + perf_flat:
        row = dict(r)
        row["_flat_uid"] = uid
        uid += 1
        flat.append(row)

    first_gen_flat, first_gen_meta = infer_first_generation_repair_failures(flat)
    flat.extend(first_gen_flat)
    first_gen_meta["first_gen_error_messages_enriched"] = _enrich_inferred_failure_messages(flat)

    db_mtime = (
        datetime.fromtimestamp(db_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        if db_path.is_file()
        else None
    )
    meta = {
        "code_history_db": str(db_path.resolve()),
        "db_mtime_local": db_mtime,
        "performance_json": str(perf_path.resolve()) if perf_path and perf_path.is_file() else None,
        "performance_mtime_local": perf_mtime,
        "provider_filter": provider,
        "records_from_history": len(hist),
        "records_from_performance": len(perf_flat),
        "records_total": len(flat),
        **first_gen_meta,
    }
    return flat, meta


def _build_aggregate_payload(
    flat: List[Dict[str, Any]],
    *,
    source_note: str,
    meta: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    exp = four_category_failure_experiment_stats(flat)
    counts = exp["counts"]
    repair_pass_before = exp["repair_pass_before"]
    repair_pass_after = exp["repair_pass_after"]
    fail_total = exp["fail_total"]
    failure_resolved_n = exp["failure_resolved_n"]
    overall_before = exp["overall_before"]
    overall_after = exp["overall_after"]

    rows: List[Dict[str, Any]] = []
    for cat in CAT_ORDER:
        c = counts[cat]
        sh = round(100.0 * c / fail_total, 2) if fail_total else 0.0
        rows.append(
            {
                "name": cat,
                "count": c,
                "share_pct": sh,
                "pass_before_pct": repair_pass_before[cat],
                "pass_after_pct": repair_pass_after[cat],
            }
        )

    multi = [r for r in flat if int(r.get("attempts") or 1) > 1]
    multi_cnt = len(multi)

    def _final_test_pass(r: Dict[str, Any]) -> bool:
        return r.get("test_success") is True

    by_case_sorted = _build_case_lists_sorted(flat)

    def _resolved(r: Dict[str, Any]) -> bool:
        return _repair_resolved_after(r, by_case_sorted, _final_test_pass)

    multi_direct_pass = sum(1 for r in multi if _final_test_pass(r))
    multi_subsequent_pass = sum(
        1 for r in multi if (not _final_test_pass(r)) and _resolved(r)
    )
    multi_resolved_n = sum(1 for r in multi if _resolved(r))

    meta = {
        **meta,
        "failure_records": fail_total,
        "failure_rows_resolved": failure_resolved_n,
        "multi_attempt_records": multi_cnt,
        "multi_direct_test_pass": multi_direct_pass,
        "multi_resolved_via_later_success": multi_subsequent_pass,
    }

    payload = {
        "title": "不同错误类型的分布与修复效果",
        "source_note": source_note,
        "rows": rows,
        "total": {
            "name": "合计",
            "count": fail_total,
            "share_pct": 100.0 if fail_total else 0.0,
            "pass_before_pct": overall_before,
            "pass_after_pct": overall_after,
        },
        "_meta": meta,
    }
    return payload, meta


def aggregate_from_history(
    db_path: Path,
    perf_path: Optional[Path] = None,
    *,
    provider: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """自 code_history ∪ llm_performance 聚合 tab:error_repair 数据。"""
    flat, merge_meta = merge_history_and_performance_flat(
        db_path=db_path,
        perf_path=perf_path,
        provider=provider,
    )
    prov_note = f" provider=`{provider}`" if provider else ""
    db_name = db_path.name
    perf_name = Path(merge_meta["performance_json"]).name if merge_meta.get("performance_json") else "（未提供）"
    db_mt = merge_meta.get("db_mtime_local") or "—"
    perf_mt = merge_meta.get("performance_mtime_local") or "—"
    payload, meta = _build_aggregate_payload(flat, source_note="", meta=merge_meta)
    fail_total = meta["failure_records"]
    fg_n = meta.get("first_gen_inferred_failures", 0)
    fg_sess = meta.get("first_gen_success_sessions", 0)
    source_note = (
        f"**历史库聚合**：`{db_name}`（修改时间 {db_mt}）∪ `{perf_name}`（修改时间 {perf_mt}）{prov_note}；"
        f"共 **{len(flat)}** 条明细（history **{merge_meta['records_from_history']}** + performance **{merge_meta['records_from_performance']}**"
        f" + 首次生成推断失败 **{fg_n}**）。"
        f"**实验总体**为 **{fail_total}** 条失败记录（含 performance 终态失败 **{fail_total - fg_n}** 条 + "
        f"首次生成内曾失败且同会话最终成功 **{fg_n}** 条，来自 **{fg_sess}** 个 ``attempts>1`` 成功会话）；"
        f"表中「数量/占比/修复前后通过率」均在该失败子集上统计。"
        f"**「修复后通过率」= 观测口径**：本条已标向量通过，或**同一 provider 且同一 (算法,模式,语言)** 在**更晚**的 history/performance 记录中出现 `test_success===true`，"
        f"或本条为首次生成推断失败（``_first_gen_repaired``）；"
        f"history 成功记录可补全「仅在 Web 落库、未写入 performance 末条」的修复。"
        f" 全库 **{meta['multi_attempt_records']}** 条 `attempts>1`；"
        f"失败样本合计 **{meta['failure_rows_resolved']}** / **{fail_total}** 条计为「后续已跑通」。"
        "「修复前通过率」：分母为**失败样本**；分子为其中本条日志即 `test_success===true` 的条数（多为 **0**）。"
        " 分类为关键词启发式，不等同于论文人工标注子集。"
    )
    payload["source_note"] = source_note
    return payload, meta


def aggregate_from_llm_performance(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    返回 (payload_for_render, meta)。
    payload 结构与 error_repair_table.json 兼容。
    """
    raw_text = path.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    flat = flatten_performance_records(data)
    mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    source_note = (
        f"**实测聚合**：自 `{path.name}`（修改时间 {mtime}），共扫描 **{len(flat)}** 条性能明细；"
        f"**实验总体**仅为失败记录（成功记录不参与本表），每条经启发式归入四类之一；"
        f"表中「数量/占比/修复前后通过率」均在该失败子集上统计。"
        f"**「修复后通过率」= 观测口径，非分类型修复流水线**：本条已标向量通过，或**同一 model_key 且同一 (算法,模式,语言)** 在**更晚**日志中出现 `test_success===true`，即计已跑通；"
        f"**不**表示对本类错误执行了专用修复规则。补跑脚本仅增加更晚成功记录。"
        "「修复前通过率」：分母为**失败样本**；分子为其中本条日志即 `test_success===true` 的条数（多为 **0**；少数仅校验失败）。"
        " 非全库首轮成功率（JSON 无首轮字段）。分类为关键词启发式，不等同于论文人工标注子集。"
    )
    payload, meta = _build_aggregate_payload(
        flat,
        source_note=source_note,
        meta={
            "performance_json": str(path.resolve()),
            "records_total": len(flat),
            "mtime_local": mtime,
        },
    )
    payload["source_note"] = (
        payload["source_note"]
        + f" 全库 **{meta['multi_attempt_records']}** 条 `attempts>1`；"
        f"其中本条即通过 **{meta['multi_direct_test_pass']}** 条，靠后续成功日志计通过 **{meta['multi_resolved_via_later_success']}** 条。"
        f" 失败样本合计 **{meta['failure_rows_resolved']}** / **{meta['failure_records']}** 条计为「后续已跑通」。"
    )
    return payload, meta
