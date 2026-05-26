#!/usr/bin/env python3
"""
从 Web/Agent 使用的 SQLite 历史库 code_history.db 汇总各 LLM 表现（与页面同一写入路径）。

说明：当前 generate 流程仅在「标准测试（及 OpenSSL 对照）通过」时调用 add_history，
故库中记录以成功样本为主；若仅用 SQLite 行均值会得到「成功率虚高」。默认 **`--rates-scope config_grid`**
：以 ``config.yaml`` 全网格 **45 槽**（DES4+AES6+RSA+SM4×3 语言）为分母，未覆盖槽按 **0%** 计。
**GSR**=该槽是否有生成落库记录，**VPR**=是否验证通过，**FTPR**=是否功能测试通过；输出满足 **GSR≥VPR≥FTPR**。
统计时将 **SQLite ``code_history``（Web 页写入的同一路径）** 与 ``llm_performance.json`` **逐条合并**（默认读取 ``experiments/results/llm_performance.json``，无则仓库根目录历史文件），不全信其一以免漏槽。

用法:
  python experiments/extract_llm_performance_from_history.py
  python experiments/extract_llm_performance_from_history.py --with-legend
  python experiments/extract_llm_performance_from_history.py --format summary
  python experiments/extract_llm_performance_from_history.py --format json
  python experiments/extract_llm_performance_from_history.py -o stats.md
  python experiments/extract_llm_performance_from_history.py --figure ./my_chart.png
  python experiments/extract_llm_performance_from_history.py --no-figure

默认：输出论文风格 **8 列 LLM 对比表**（与 tab:llm_performance 版式一致：左对齐首列、数值列居中）。
加 `--with-legend` 时在表前附加指标说明；`--format summary` 为按 provider 聚合的历史简表。
**`--format paper`（默认）时**：除非 **`--no-figure`**，否则自动写出 **2×2 性能概览图**，默认路径为 **`experiments/results/llm_performance_overview.pdf`**（矢量 PDF）。
可用 **`--figure 路径`** 指定其它输出文件（扩展名决定格式，如 `.png`）。需已安装 `matplotlib`。
运行时在 stderr 会说明图表路径。
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sqlite3
import sys
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
_EXPERIMENTS_DIR = Path(__file__).resolve().parent
EXPERIMENTS_RESULTS_DIR = _EXPERIMENTS_DIR / "results"
DEFAULT_LLM_PERFORMANCE_FIGURE = EXPERIMENTS_RESULTS_DIR / "llm_performance_overview.pdf"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.experiment_outputs import resolve_under_results  # noqa: E402


def _default_llm_performance_json_path() -> Optional[Path]:
    primary = resolve_under_results(Path("llm_performance.json"))
    if primary.is_file():
        return primary
    legacy = ROOT / "llm_performance.json"
    return legacy if legacy.is_file() else None

from agent.prompt_builder import build_prompt  # noqa: E402
from utils.config_loader import ConfigLoader  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402
from utils.prompt_loader import PromptLoader  # noqa: E402
from utils.test_data_loader import TestDataLoader  # noqa: E402


class _DryRunAgent:
    """与 experiments/run_prompt_ablation 一致，用于统计 prompt 字符长度（不初始化 LLM）。"""

    def __init__(self, provider: str, config_path: str = "config.yaml"):
        self.provider = provider
        self.config = ConfigLoader(config_path)
        self.prompt_loader = PromptLoader()
        self.openssl_dev_available = False
        self.test_data_loader = TestDataLoader()


DEFAULT_PAPER_PROVIDER_ORDER = [
    "openai",
    "deepseek",
    "doubao",
    "claude",
    "codex",
    "qwen_coder_local",
]


def _norm_lang_key(lang: Optional[str]) -> Optional[str]:
    if not lang:
        return None
    x = lang.strip().lower()
    if x in ("c++", "cpp"):
        return "cpp"
    if x == "c":
        return "c"
    if x == "python":
        return "python"
    return None


def _clamp_mono_gsr_vpr_ftpr(gsr: float, vpr: float, ftpr: float) -> Tuple[float, float, float]:
    """保证展示满足 GSR ≥ VPR ≥ FTPR（舍入或脏数据导致倒置时钳位）。"""
    g = float(gsr)
    v = min(float(vpr), g)
    t = min(float(ftpr), v)
    g = max(g, v)
    return (round(g, 2), round(v, 2), round(t, 2))


def _sql_avg_gen_time_by_lang(conn: sqlite3.Connection) -> Dict[str, Dict[str, float]]:
    """provider -> { python|c|cpp -> avg_generation_time }"""
    cur = conn.execute(
        """
        SELECT provider, language, AVG(generation_time) AS agt
        FROM code_history
        GROUP BY provider, language
        """
    )
    out: Dict[str, Dict[str, float]] = {}
    for row in cur.fetchall():
        prov = (row["provider"] or "").strip() or "unknown"
        nk = _norm_lang_key(row["language"])
        if not nk:
            continue
        gt = row["agt"]
        out.setdefault(prov, {})[nk] = round(float(gt), 2) if gt is not None else 0.0
    return out


def _merge_llm_performance_by_provider(raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """按 provider 合并 llm_performance.json 中各 model_key。"""
    merged: Dict[str, Dict[str, Any]] = {}
    for _mk, md in raw.items():
        prov = (md.get("provider") or "unknown").strip()
        bucket = merged.setdefault(
            prov,
            {
                "validation_success": 0,
                "validation_failure": 0,
                "test_success": 0,
                "test_failure": 0,
                "total_generation_time": 0.0,
                "total_attempts": 0,
                "records": [],
            },
        )
        bucket["validation_success"] += int(md.get("validation_success") or 0)
        bucket["validation_failure"] += int(md.get("validation_failure") or 0)
        bucket["test_success"] += int(md.get("test_success") or 0)
        bucket["test_failure"] += int(md.get("test_failure") or 0)
        bucket["total_generation_time"] += float(md.get("total_generation_time") or 0.0)
        bucket["total_attempts"] += int(md.get("total_attempts") or 0)
        bucket["records"].extend(md.get("records") or [])
    return merged


def _effective_records_for_provider(
    bucket: Optional[Dict[str, Any]],
    db_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """合并 **SQLite code_history（Web 历史）** 与 ``llm_performance.json`` 的逐条明细。

    二者顺序拼接（库在前、JSON 在后），槽位统计上对同一算法×模式×语言取并集，
    避免「仅有 JSON 汇总时漏掉只在库里出现过的 AES/RSA/SM4」等情况。
    """
    json_recs: List[Dict[str, Any]] = []
    if bucket:
        json_recs = list(bucket.get("records") or [])
    return list(db_records) + json_recs


def _slot_key_record(r: Dict[str, Any]) -> Tuple[str, str, str]:
    """论文表口径：槽位 = 算法 × 模式 × 语言（与历史库 normalize_case_key 一致）。"""
    lk = _norm_lang_key(r.get("language"))
    lang = lk if lk else (str(r.get("language") or "").strip().lower() or "?")
    return HistoryManager.normalize_case_key(r.get("algorithm"), r.get("mode"), lang)


def build_reference_slot_keys(cfg: ConfigLoader) -> Set[Tuple[str, str, str]]:
    """与 Web 批量一致的全网格槽位集合（config.yaml：算法 × 模式 × 语言）。"""
    langs = list(cfg.get("supported_languages") or ["python", "c", "cpp"])
    algs = list(cfg.get("crypto_algorithms") or [])
    des_modes = list(cfg.get("des_modes") or [])
    aes_modes = list(cfg.get("aes_modes") or [])
    sm4_modes = list(cfg.get("sm4_modes") or des_modes)
    keys: Set[Tuple[str, str, str]] = set()
    for alg in algs:
        u = (alg or "").strip().upper()
        if u == "RSA":
            for lang in langs:
                lk = _norm_lang_key(lang) or lang
                keys.add(HistoryManager.normalize_case_key(u, None, lk))
        elif u == "AES":
            for mode in aes_modes:
                for lang in langs:
                    lk = _norm_lang_key(lang) or lang
                    keys.add(HistoryManager.normalize_case_key(u, mode, lk))
        elif u in ("DES", "SM4"):
            modes = des_modes if u == "DES" else sm4_modes
            for mode in modes:
                for lang in langs:
                    lk = _norm_lang_key(lang) or lang
                    keys.add(HistoryManager.normalize_case_key(u, mode, lk))
    return keys


def _rates_slot_based_from_records(
    records: List[Dict[str, Any]],
    reference_slots: Optional[Set[Tuple[str, str, str]]] = None,
) -> Tuple[float, float, float]:
    """按槽位二值再平均；最后对槽取算术平均（与「按尝试次数加权」不同）。

    - **GSR（生成成功率）**：该槽在明细中**至少有一条落库记录**（至少完成一次生成尝试）则 100%，否则 0%。
    - **VPR**：该槽**至少有一条** ``validation_success is True`` 则 100%，否则 0%。
    - **FTPR**：该槽**至少有一条** ``test_success is True`` 则 100%（且要求 ``t_ok => v_ok``，避免倒置）。

    故恒有单槽 **GSR ≥ VPR ≥ FTPR**；全网格上再取平均后仍对返回值做单调钳位。

    ``reference_slots`` 非空时：分母固定为 **config 全网格**（与 Web 批量一致：DES4+AES6+RSA+SM4 各模式 × 3 语言 = 45 槽）；
    未出现在 records 的槽 GSR/VPR/FTPR 均记 0%。
    """
    from collections import defaultdict

    slots: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        slots[_slot_key_record(r)].append(r)

    def _acc_one(rs: List[Dict[str, Any]]) -> Tuple[float, float, float]:
        g_ok = len(rs) > 0
        v_ok = any(x.get("validation_success") is True for x in rs)
        t_ok = any(x.get("test_success") is True for x in rs)
        if t_ok and not v_ok:
            t_ok = False
        g_acc = 100.0 if g_ok else 0.0
        v_acc = 100.0 if v_ok else 0.0
        t_acc = 100.0 if t_ok else 0.0
        return g_acc, v_acc, t_acc

    if reference_slots is not None:
        n = len(reference_slots)
        if n == 0:
            return 0.0, 0.0, 0.0
        g_acc = v_acc = t_acc = 0.0
        for sk in reference_slots:
            rs = slots.get(sk, [])
            ga, va, ta = _acc_one(rs)
            g_acc += ga
            v_acc += va
            t_acc += ta
        return (
            round(g_acc / n, 2),
            round(v_acc / n, 2),
            round(t_acc / n, 2),
        )

    if not slots:
        return 0.0, 0.0, 0.0
    n = len(slots)
    g_acc = v_acc = t_acc = 0.0
    for rs in slots.values():
        ga, va, ta = _acc_one(rs)
        g_acc += ga
        v_acc += va
        t_acc += ta
    return (
        round(g_acc / n, 2),
        round(v_acc / n, 2),
        round(t_acc / n, 2),
    )


def _ersr_first_gen_pass_from_records(records: List[Dict[str, Any]]) -> float:
    """
    首次生成即通过率（论文列仍记 FGPR / 原 ERSR 列位）：

    在合并明细中，``attempts==1`` 的生成会话（当次点击生成未触发会话内多轮 Self-Refine）
    里 ``test_success===true`` 的占比。若无 ``attempts==1`` 记录，记 **100%**。
    """
    first_gen = [r for r in records if int(r.get("attempts") or 1) == 1]
    if not first_gen:
        return 100.0
    ok = sum(1 for r in first_gen if r.get("test_success") is True)
    return round(100.0 * ok / len(first_gen), 2)


def _rates_from_perf_bucket(
    bucket: Dict[str, Any],
    reference_slots: Optional[Set[Tuple[str, str, str]]] = None,
) -> Tuple[float, float, float, float]:
    """返回 GSR, VPR, FTPR, FGPR(%)。

    优先使用 ``records`` 做 **槽位（算法×模式×语言）** 级二值再平均；无明细记录时回退为旧版「成功次数/（成功+失败）」加权比。
    第四项 **FGPR（首次生成即通过率）**：``attempts==1`` 的会话中 ``test_success===true`` 占比；
    **若无 ``attempts==1`` 记录**，记 **100%**。返回值键名仍为 ``ersr`` 以兼容作图脚本。
    """
    records = bucket.get("records") or []
    if records:
        gsr, vpr, ftpr = _rates_slot_based_from_records(records, reference_slots)
    elif reference_slots is not None:
        gsr = vpr = ftpr = 0.0
    else:
        vs, vf = bucket.get("validation_success", 0), bucket.get("validation_failure", 0)
        ts, tf = bucket.get("test_success", 0), bucket.get("test_failure", 0)
        denom_v = vs + vf
        denom_t = ts + tf
        vpr = round(100.0 * vs / denom_v, 2) if denom_v > 0 else 100.0
        # 无明细时无法区分「生成尝试」与验证；沿用旧式并让 GSR 与 VPR 同值
        gsr = vpr
        ftpr = round(100.0 * ts / denom_t, 2) if denom_t > 0 else 100.0

    if records:
        ersr = _ersr_first_gen_pass_from_records(records)
    else:
        ersr = 100.0
    gsr, vpr, ftpr = _clamp_mono_gsr_vpr_ftpr(gsr, vpr, ftpr)
    return gsr, vpr, ftpr, ersr


def _agt_from_bucket(bucket: Dict[str, Any]) -> float:
    ta = bucket.get("total_attempts") or 0
    tt = bucket.get("total_generation_time") or 0.0
    if ta > 0:
        return round(tt / ta, 2)
    return 0.0


def _prompt_lens_chars(provider: str, config_path: Path) -> Dict[str, int]:
    """DES-ECB 全量 prompt 字符数（与 Web full 一致）。"""
    agent = _DryRunAgent(provider, config_path=str(config_path))
    td = None
    if agent.test_data_loader:
        td = agent.test_data_loader.get_test_data("DES", "ECB")
    out: Dict[str, int] = {}
    for lang in ("python", "c", "cpp"):
        text = build_prompt(
            agent,
            "DES",
            mode="ECB",
            operation="加密解密",
            language=lang,
            test_data=td,
        )
        out[lang] = len(text)
    return out


def _fmt_pct_one(x: float) -> str:
    """百分比：一位小数；恰为 100 时写 ``100``（不写 ``100.0``）。"""
    xf = float(x)
    if abs(xf - 100.0) < 1e-6:
        return "100"
    return f"{xf:.1f}"


def _fmt_pct_two(x: float) -> str:
    """FTPR / 部分百分比两位小数；恰为 100 时写 ``100``。"""
    xf = float(x)
    if abs(xf - 100.0) < 1e-6:
        return "100"
    return f"{xf:.2f}"


def _fmt_agt(x: float) -> str:
    return f"{float(x):.2f}"


def _fmt_ftp(x: float) -> str:
    """FTPR：接近 100 时用整数形式 ``100``，否则保留两位（如 9.39）。"""
    x = float(x)
    if x >= 99.95:
        return _fmt_pct_one(x)
    return _fmt_pct_two(x)


def _fmt_tri_time(tp: Optional[float], tc: Optional[float], tcpp: Optional[float]) -> str:
    def one(v: Optional[float]) -> str:
        if v is None:
            return "N/A"
        return f"{float(v):.1f}"

    return "/".join(one(x) for x in (tp, tc, tcpp))


def _display_llm_row_name(provider: str, cfg: ConfigLoader) -> str:
    pretty = {
        "openai": "OpenAI",
        "deepseek": "Deepseek",
        "doubao": "Doubao",
        "claude": "Claude",
        "codex": "CodeX",
        "qwen_coder_local": (cfg.get_llm_config("qwen_coder_local") or {}).get("model")
        or "qwen2.5-coder:7b",
    }
    if provider in pretty:
        return pretty[provider]
    return provider.replace("_", " ").title()


def _group_records_by_provider(
    conn: sqlite3.Connection,
) -> Dict[str, List[Dict[str, Any]]]:
    """从 code_history 取出各 provider 的逐条记录，供与 llm_performance.json 槽位统计对齐。"""
    from collections import defaultdict

    cur = conn.execute(
        """
        SELECT provider, algorithm, mode, language, validation_success, test_success,
               attempts, generation_time
        FROM code_history
        """
    )
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in cur.fetchall():
        prov = (row["provider"] or "").strip() or "unknown"
        ts = row["test_success"]
        if ts is None:
            ts_out: Optional[bool] = None
        else:
            ts_out = bool(ts)
        vs = row["validation_success"]
        v_out = True if vs else False
        out[prov].append(
            {
                "algorithm": row["algorithm"],
                "mode": row["mode"],
                "language": row["language"],
                "validation_success": v_out,
                "test_success": ts_out,
                "attempts": int(row["attempts"] or 1),
                "generation_time": float(row["generation_time"] or 0.0),
            }
        )
    return dict(out)


def collect_paper_llm_metrics(
    db_path: Path,
    config_path: Path,
    performance_json: Optional[Path],
    provider_order: Optional[List[str]],
    *,
    rates_scope: str = "config_grid",
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    与论文 8 列表同行数据：便于 Markdown 渲染与 matplotlib 绘图共用同一来源。
    每条记录含 display_name、GSR/VPR/FTPR/ERSR、AGT、DES-ECB 全量 prompt 字符数、按语言平均生成时间。

    ``rates_scope``:
    - ``config_grid``（默认）：分母为 ``config.yaml`` 全网格槽位（通常 **45**）；未覆盖记 0%。
      明细为 **code_history.db ∪ llm_performance.json**（按 provider 合并后再算槽位）。
      **GSR** 按「是否有落库尝试」、**VPR** 按验证、**FTPR** 按功能测试，并对三者做单调钳位。
    - ``recorded_slots``：仅在 **records 中出现过的槽** 上取平均（旧口径，稀疏样本易虚高）。
    """
    for log_name in ("CryptoAgent", "utils", "agent"):
        logging.getLogger(log_name).setLevel(logging.WARNING)

    cfg = ConfigLoader(str(config_path))
    ref_grid: Optional[Set[Tuple[str, str, str]]] = None
    if rates_scope == "config_grid":
        ref_grid = build_reference_slot_keys(cfg)
    elif rates_scope != "recorded_slots":
        raise ValueError(
            f"rates_scope 须为 config_grid 或 recorded_slots，收到: {rates_scope!r}"
        )

    perf_by_prov: Dict[str, Dict[str, Any]] = {}
    perf_err: Optional[str] = None
    if performance_json and performance_json.is_file():
        try:
            perf_raw = json.loads(performance_json.read_text(encoding="utf-8"))
            perf_by_prov = _merge_llm_performance_by_provider(perf_raw)
        except (OSError, json.JSONDecodeError) as e:
            perf_by_prov = {}
            perf_err = str(e)

    with _open_db(db_path) as conn:
        lang_times = _sql_avg_gen_time_by_lang(conn)
        cur = conn.execute("SELECT DISTINCT provider FROM code_history")
        db_providers = {str(r[0]).strip() for r in cur.fetchall() if r[0]}
        db_by_prov = _group_records_by_provider(conn)

    cfg_providers = [
        p
        for p, c in (cfg._config.get("llm_providers") or {}).items()
        if c.get("enabled", False)
    ]

    union_p = (
        set(perf_by_prov)
        | set(lang_times.keys())
        | db_providers
        | set(cfg_providers)
    )
    order = list(provider_order or DEFAULT_PAPER_PROVIDER_ORDER)
    providers_out: List[str] = []
    seen = set()
    for p in order:
        if p in union_p and p not in seen:
            providers_out.append(p)
            seen.add(p)
    for p in sorted(union_p):
        if p not in seen:
            providers_out.append(p)
            seen.add(p)

    rows: List[Dict[str, Any]] = []
    for prov in providers_out:
        pl_py = pl_c = pl_cpp = None
        try:
            lens = _prompt_lens_chars(prov, config_path)
            pl_py, pl_c, pl_cpp = lens["python"], lens["c"], lens["cpp"]
        except Exception:
            pass

        lt = lang_times.get(prov, {})
        tp = lt.get("python")
        tc = lt.get("c")
        tcpp = lt.get("cpp")

        bucket = perf_by_prov.get(prov)
        eff_records = _effective_records_for_provider(bucket, db_by_prov.get(prov, []))
        eff_bucket: Dict[str, Any] = dict(bucket) if bucket else {}
        eff_bucket["records"] = eff_records

        agt = 0.0
        if eff_records:
            gsr, vpr, ftpr, ersr = _rates_from_perf_bucket(eff_bucket, ref_grid)
        elif ref_grid is not None:
            gsr = vpr = ftpr = 0.0
            ersr = 100.0
        elif bucket:
            gsr, vpr, ftpr, ersr = _rates_from_perf_bucket(eff_bucket, None)
        else:
            with _open_db(db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT AVG(CASE WHEN validation_success THEN 1.0 ELSE 0.0 END),
                           AVG(CASE WHEN test_success THEN 1.0 ELSE 0.0 END),
                           AVG(generation_time)
                    FROM code_history WHERE provider = ?
                    """,
                    (prov,),
                )
                row = cur.fetchone()
                cur_e = conn.execute(
                    """
                    SELECT AVG(CASE WHEN test_success THEN 1.0 ELSE 0.0 END)
                    FROM code_history
                    WHERE provider = ? AND COALESCE(attempts, 1) = 1
                    """,
                    (prov,),
                )
                er_row = cur_e.fetchone()
            if row and row[0] is not None:
                vpr = round(100.0 * float(row[0]), 2)
                gsr = vpr
                ftpr = round(100.0 * float(row[1]), 2)
                gsr, vpr, ftpr = _clamp_mono_gsr_vpr_ftpr(gsr, vpr, ftpr)
                agt = float(row[2]) if row[2] is not None else 0.0
            else:
                gsr = vpr = ftpr = 0.0
                ersr = 100.0
            if er_row and er_row[0] is not None:
                ersr = round(100.0 * float(er_row[0]), 2)
            else:
                ersr = 100.0

        if bucket and int(bucket.get("total_attempts") or 0) > 0:
            agt = _agt_from_bucket(bucket)
        elif eff_records:
            ta = sum(int(r.get("attempts") or 1) for r in eff_records)
            tt = sum(float(r.get("generation_time") or 0.0) for r in eff_records)
            if ta > 0:
                agt = round(tt / ta, 2)

        rows.append(
            {
                "provider": prov,
                "display_name": _display_llm_row_name(prov, cfg),
                "gsr": float(gsr),
                "vpr": float(vpr),
                "ftpr": float(ftpr),
                "ersr": float(ersr),
                "agt": float(agt),
                "prompt_len_python": pl_py,
                "prompt_len_c": pl_c,
                "prompt_len_cpp": pl_cpp,
                "time_python": tp,
                "time_c": tc,
                "time_cpp": tcpp,
            }
        )

    return rows, perf_err


def save_llm_performance_figure(
    rows: List[Dict[str, Any]],
    out_path: Path,
    *,
    dpi: int = 150,
    figsize: Tuple[float, float] = (16.0, 11.5),
) -> None:
    """生成与论文表配套的 2×2 柱状图（通过率 / AGT / 提示长度 / 按语言耗时）。"""
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as e:
        raise RuntimeError(
            "绘图需要 matplotlib，请执行: pip install matplotlib"
        ) from e

    if not rows:
        raise ValueError("无可用指标行，跳过绘图")

    # 放大坐标轴/图例/刻度默认字号；柱顶数字单独设，避免 4 组柱条时挤成一团
    _rc = {
        "font.size": 12,
        "axes.titlesize": 14,
        "axes.labelsize": 13,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "legend.fontsize": 11,
    }

    labels = [str(r["display_name"]) for r in rows]
    n = len(labels)
    x = np.arange(n)
    # 类目间距为 1.0；柱簇总宽必须 < 1，否则相邻类目上的柱会互相压住（旧版 1.008 会略重叠）
    _group_fill = 0.9999
    width = (1.0 / 4.0) * _group_fill
    w2 = (1.0 / 3.0) * _group_fill

    plt.rcParams.update(_rc)
    fig, axes = plt.subplots(2, 2, figsize=figsize, constrained_layout=True)

    # 图1 通过率：用户三色 + ERSR（#ffc09f 与 #ffee93 的间色，便于区分第四条）
    c_gsr = "#EFCEE8"
    c_vpr = "#F3D7B5"
    c_ftpr = "#DAF9CA"
    c_ersr = "#C7B3E5"
    # 图3 提示长度（三语言）
    c_len_py, c_len_c, c_len_cpp = "#DEF9AE", "#8BD6C0", "#7EAF67"
    # 图4 分语言耗时（三语言）
    c_time_py, c_time_c, c_time_cpp = "#B7F7FF", "#70B5FF", "#1F47AD"

    # --- (0,0) GSR / VPR / FTPR / ERSR ---
    ax0 = axes[0, 0]
    gsr = [r["gsr"] for r in rows]
    vpr = [r["vpr"] for r in rows]
    ftpr = [r["ftpr"] for r in rows]
    ersr_heights: List[float] = []
    ersr_na: List[bool] = []
    for r in rows:
        e = r.get("ersr")
        if e is None:
            ersr_heights.append(0.0)
            ersr_na.append(True)
        else:
            ersr_heights.append(float(e))
            ersr_na.append(False)

    r1 = ax0.bar(
        x - 1.5 * width, gsr, width, label="GSR (%)", color=c_gsr, edgecolor="none", linewidth=0
    )
    r2 = ax0.bar(
        x - 0.5 * width, vpr, width, label="VPR (%)", color=c_vpr, edgecolor="none", linewidth=0
    )
    r3 = ax0.bar(
        x + 0.5 * width, ftpr, width, label="FTPR (%)", color=c_ftpr, edgecolor="none", linewidth=0
    )
    r4 = ax0.bar(
        x + 1.5 * width,
        ersr_heights,
        width,
        label="FGPR (%)",
        color=c_ersr,
        edgecolor="none",
        linewidth=0,
    )

    _bar_label_fs = 9  # 柱顶数值（论文插图建议 ≥9pt 量级，便于缩放后仍可读）

    def _bar_pct_txt(val: float) -> str:
        v = float(val)
        if abs(v - 100.0) < 1e-6:
            return "100"
        return f"{v:.1f}"

    for rects, vals in ((r1, gsr), (r2, vpr), (r3, ftpr)):
        for rect, val in zip(rects, vals):
            h = rect.get_height()
            ax0.annotate(
                _bar_pct_txt(val),
                xy=(rect.get_x() + rect.get_width() / 2, h),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=_bar_label_fs,
            )
    for rect, na, eh in zip(r4, ersr_na, ersr_heights):
        if na:
            ax0.annotate(
                "N/A",
                xy=(rect.get_x() + rect.get_width() / 2, 2),
                ha="center",
                va="bottom",
                fontsize=_bar_label_fs,
            )
        else:
            h = rect.get_height()
            ax0.annotate(
                _bar_pct_txt(eh),
                xy=(rect.get_x() + rect.get_width() / 2, h),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=_bar_label_fs,
            )

    ax0.set_ylabel("Pass Rate (%)")
    ax0.set_title("LLM Pass Rate by Mode (DES/AES/RSA/SM4)")
    ax0.set_xticks(x)
    ax0.set_xticklabels(labels, rotation=18, ha="right")
    ax0.set_ylim(0, 105)
    ax0.legend(loc="lower left", ncol=2)
    ax0.grid(axis="y", linestyle="--", alpha=0.35)

    # --- (0,1) Average generation time (AGT) ---
    ax1 = axes[0, 1]
    agts = [r["agt"] for r in rows]
    bars_agt = ax1.bar(
        x, agts, width=_group_fill, color="#42516B", edgecolor="none", linewidth=0
    )
    _agt_cycle = ["#42516B", "#428070", "#94D6D5", "#FAE3FC", "#A3FFFF", "#FFE9A0"]
    nb = len(bars_agt)
    for i, b in enumerate(bars_agt):
        b.set_facecolor(_agt_cycle[i % len(_agt_cycle)])
    for rect, v in zip(bars_agt, agts):
        ax1.annotate(
            f"{v:.1f}",
            xy=(rect.get_x() + rect.get_width() / 2, rect.get_height()),
            xytext=(0, 2),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=11,
        )
    ax1.set_ylabel("AGT (s)")
    ax1.set_title("LLM Average Generation Time")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=18, ha="right")
    ymax = max(agts) * 1.12 if agts else 1.0
    ax1.set_ylim(0, ymax)
    ax1.grid(axis="y", linestyle="--", alpha=0.35)

    # --- (1,0) Prompt length by language ---
    ax2 = axes[1, 0]
    py_i = [r.get("prompt_len_python") for r in rows]
    c_i = [r.get("prompt_len_c") for r in rows]
    cpp_i = [r.get("prompt_len_cpp") for r in rows]
    py_v = [float(v) if v is not None else 0.0 for v in py_i]
    c_v = [float(v) if v is not None else 0.0 for v in c_i]
    cpp_v = [float(v) if v is not None else 0.0 for v in cpp_i]

    b_py = ax2.bar(
        x - w2, py_v, w2, label="Python", color=c_len_py, edgecolor="none", linewidth=0
    )
    b_c = ax2.bar(x, c_v, w2, label="C", color=c_len_c, edgecolor="none", linewidth=0)
    b_cpp = ax2.bar(x + w2, cpp_v, w2, label="C++", color=c_len_cpp, edgecolor="none", linewidth=0)

    def _lbl_int(rects, raw_vals: List[Optional[int]]):
        for rect, raw in zip(rects, raw_vals):
            if raw is None:
                continue
            h = rect.get_height()
            if h <= 0:
                continue
            ax2.annotate(
                f"{int(raw)}",
                xy=(rect.get_x() + rect.get_width() / 2, h),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=10,
            )

    _lbl_int(b_py, [int(v) if v is not None else None for v in py_i])
    _lbl_int(b_c, [int(v) if v is not None else None for v in c_i])
    _lbl_int(b_cpp, [int(v) if v is not None else None for v in cpp_i])

    ax2.set_ylabel("Length (chars)")
    ax2.set_title("Prompt Length by Language")
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=18, ha="right")
    ax2.legend(loc="upper right")
    ax2.grid(axis="y", linestyle="--", alpha=0.35)
    y2m = max(py_v + c_v + cpp_v) * 1.1 if (py_v + c_v + cpp_v) else 1.0
    ax2.set_ylim(0, max(y2m, 1.0))

    # --- (1,1) Generation time by language ---
    ax3 = axes[1, 1]
    tpy = [r.get("time_python") for r in rows]
    tc = [r.get("time_c") for r in rows]
    tcpp = [r.get("time_cpp") for r in rows]
    tpy_v = [float(v) if v is not None else 0.0 for v in tpy]
    tc_v = [float(v) if v is not None else 0.0 for v in tc]
    tcpp_v = [float(v) if v is not None else 0.0 for v in tcpp]

    bt1 = ax3.bar(
        x - w2, tpy_v, w2, label="Python", color=c_time_py, edgecolor="none", linewidth=0
    )
    bt2 = ax3.bar(x, tc_v, w2, label="C", color=c_time_c, edgecolor="none", linewidth=0)
    bt3 = ax3.bar(x + w2, tcpp_v, w2, label="C++", color=c_time_cpp, edgecolor="none", linewidth=0)

    def _lbl_time(rects, raw_vals: List[Optional[float]]):
        for rect, raw in zip(rects, raw_vals):
            if raw is None:
                continue
            h = rect.get_height()
            if h <= 0:
                continue
            ax3.annotate(
                f"{float(raw):.1f}",
                xy=(rect.get_x() + rect.get_width() / 2, h),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=10,
            )

    _lbl_time(bt1, tpy)
    _lbl_time(bt2, tc)
    _lbl_time(bt3, tcpp)

    ax3.set_ylabel("Generation Time (s)")
    ax3.set_title("Generation Time by Language")
    ax3.set_xticks(x)
    ax3.set_xticklabels(labels, rotation=18, ha="right")
    ax3.legend(loc="upper left", framealpha=0.92)
    ax3.grid(axis="y", linestyle="--", alpha=0.35)
    y3m = max(tpy_v + tc_v + tcpp_v) * 1.1 if (tpy_v + tc_v + tcpp_v) else 1.0
    ax3.set_ylim(0, max(y3m, 1.0))

    # 收紧横轴：去掉类目轴默认留白，使最左/最右柱贴近坐标区边界
    # 与 ax0 四柱布局一致：最左柱左缘 = -2*width，最右柱右缘 = (n-1)+2*width
    _x_lo = -2.0 * width
    _x_hi = float(n - 1) + 2.0 * width
    for ax in (ax0, ax1, ax2, ax3):
        ax.margins(x=0)
        ax.set_xlim(_x_lo, _x_hi)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def render_paper_llm_table(
    db_path: Path,
    config_path: Path,
    performance_json: Optional[Path],
    provider_order: Optional[List[str]],
    *,
    table_only: bool = True,
    precomputed: Optional[Tuple[List[Dict[str, Any]], Optional[str]]] = None,
    rates_scope: str = "config_grid",
) -> str:
    """生成论文用 LLM 性能对比 Markdown 表（8 列）。

    若已在主流程中调用过 ``collect_paper_llm_metrics``，可传入 ``precomputed`` 避免重复统计。
    """
    if precomputed is not None:
        rows_m, perf_err = precomputed
    else:
        rows_m, perf_err = collect_paper_llm_metrics(
            db_path,
            config_path,
            performance_json,
            provider_order,
            rates_scope=rates_scope,
        )

    header_row = (
        "| LLM | 提示长度 (Python/C/C++) | 生成时间 (Python/C/C++) (s) | "
        "GSR (%) | VPR (%) | FTPR (%) | FGPR (%) | AGT (s) |"
    )
    # 首列左对齐，其余列居中（GitHub / VS Code / Typora 常见渲染）
    sep_row = (
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    )

    lines: List[str] = []
    if not table_only:
        lines.extend(
            [
                "## 所提方法在不同 LLM 上的整体性能",
                "",
                "**指标说明**",
                "",
                "- **生成成功率（GSR）**：默认 **`config_grid`**——分母为 ``config.yaml`` **全网格 45 槽**（DES 4 模式 + AES 6 模式 + RSA + SM4 4 模式，各 × python/c/cpp）；某槽在合并明细（`llm_performance.json` + SQLite）中**至少有一条落库记录**则该槽 GSR 记 **100%**，否则 **0%**。",
                "- **验证通过率（VPR）**：同上全网格分母；某槽**至少有一条**验证通过则该槽记 **100%**。恒满足 **GSR ≥ VPR**（有验证必先有生成尝试）。加 **`--rates-scope recorded_slots`** 时仅在「出现过记录的槽」上平均（旧口径）。",
                "- **功能测试通过率（FTPR）**：同上；某槽**至少有一条**功能测试通过则 **100%**，且 **FTPR ≤ VPR**（测试通过须先通过验证）。输出前做单调钳位，保证 **GSR ≥ VPR ≥ FTPR**。无明细且非全网格时回退为历史计数比。",
                "- **首次生成即通过率（FGPR，列键 ``ersr``）**：合并明细中 ``attempts==1`` 的会话里 ``test_success===true`` 占比（当次生成未触发会话内多轮重试）。**若无 ``attempts==1`` 记录**，记 **100%**。",
                "- **平均生成时间（AGT）**：每测试用例的平均生成时间（秒）。",
                "",
                f"- **数据库**: `{db_path}`",
                f"- **配置文件**: `{config_path}`（提示长度：DES-ECB 全量 prompt 字符数）",
            ]
        )
        if performance_json:
            lines.append(
                f"- **性能日志**: `{performance_json}`（GSR/VPR/FTPR/ERSR/AGT 优先从此合并）"
            )
        if perf_err:
            lines.append(f"- **警告**: 未能读取性能 JSON: {perf_err}")
        lines.append("")

    lines.extend([header_row, sep_row])

    for row in rows_m:
        if row.get("prompt_len_python") is not None:
            tri_len = f"{row['prompt_len_python']}/{row['prompt_len_c']}/{row['prompt_len_cpp']}"
        else:
            tri_len = "N/A/N/A/N/A"
        tri_t = _fmt_tri_time(
            row.get("time_python"),
            row.get("time_c"),
            row.get("time_cpp"),
        )
        er = row.get("ersr")
        ersr_s = _fmt_pct_two(er) if er is not None else "N/A"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["display_name"]),
                    tri_len,
                    tri_t,
                    _fmt_pct_one(row["gsr"]),
                    _fmt_pct_one(row["vpr"]),
                    _fmt_ftp(row["ftpr"]),
                    ersr_s,
                    _fmt_agt(row["agt"]),
                ]
            )
            + " |"
        )

    if not table_only:
        lines.extend(
            [
                "",
                "*生成时间列为历史库按语言的平均 `generation_time`；缺语言数据时显示 N/A。提示长度为 DES-ECB 全量 prompt 字符数。*",
            ]
        )
    return "\n".join(lines)


def _open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _model_from_row(row: sqlite3.Row) -> str:
    raw = row["extra_data"] if "extra_data" in row.keys() else None
    if not raw:
        return ""
    try:
        ex = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ""
    for k in ("model", "model_name", "llm_model"):
        v = ex.get(k)
        if v:
            return str(v)
    return ""


def aggregate(
    db_path: Path, by_model: bool
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    with _open_db(db_path) as conn:
        cur = conn.execute("SELECT COUNT(*) AS c FROM code_history")
        total_rows = cur.fetchone()[0]

        rows = conn.execute(
            """
            SELECT provider, validation_success, test_success, generation_time,
                   attempts, extra_data
            FROM code_history
            """
        ).fetchall()

    key_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def bump(key: Tuple[str, str], r: sqlite3.Row) -> None:
        st = key_stats.setdefault(
            key,
            {
                "provider": key[0],
                "model_bucket": key[1],
                "n": 0,
                "validation_ok": 0,
                "test_ok": 0,
                "generation_times": [],
                "attempts": [],
            },
        )
        st["n"] += 1
        if r["validation_success"]:
            st["validation_ok"] += 1
        if r["test_success"]:
            st["test_ok"] += 1
        gt = r["generation_time"]
        if gt is not None:
            st["generation_times"].append(float(gt))
        at = r["attempts"]
        if at is not None:
            st["attempts"].append(int(at))

    for r in rows:
        prov = (r["provider"] or "").strip() or "unknown"
        if by_model:
            mb = _model_from_row(r) or "__default__"
        else:
            mb = ""
        bump((prov, mb), r)

    out: List[Dict[str, Any]] = []
    for (_, _), st in sorted(key_stats.items(), key=lambda x: (x[0][0], x[0][1])):
        gt = st.pop("generation_times")
        at = st.pop("attempts")
        row = dict(st)
        row["avg_generation_time_s"] = round(mean(gt), 4) if gt else None
        row["avg_attempts"] = round(mean(at), 3) if at else None
        row["validation_rate"] = (
            round(row["validation_ok"] / row["n"], 4) if row["n"] else 0.0
        )
        row["test_rate"] = round(row["test_ok"] / row["n"], 4) if row["n"] else 0.0
        out.append(row)

    meta = {
        "db_path": str(db_path),
        "table": "code_history",
        "total_rows": total_rows,
        "note": "成功写入历史的样本；若需含失败请求，需扩展 add_history 调用或另建日志。",
    }
    return out, meta


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _cell_md(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("|", "\\|")
    return " ".join(s.splitlines()) if "\n" in s else s


def _render_markdown_table(
    rows: List[Dict[str, Any]], meta: Dict[str, Any], by_model: bool
) -> str:
    """生成可粘贴进论文/文档的 Markdown（说明 + 表格）。"""
    lines: List[str] = [
        "## LLM 历史表现汇总（`code_history`）",
        "",
        f"- **数据库**: `{meta['db_path']}`",
        f"- **表**: `{meta['table']}`，原始记录数: **{meta['total_rows']}**",
        f"- **说明**: {meta['note']}",
        "",
    ]
    if not rows:
        lines.append("*（无聚合数据）*")
        return "\n".join(lines)

    preferred = [
        "provider",
        "model_bucket",
        "n",
        "validation_ok",
        "test_ok",
        "validation_rate",
        "test_rate",
        "avg_generation_time_s",
        "avg_attempts",
    ]
    keys = list(rows[0].keys())
    if not by_model:
        keys = [k for k in keys if k != "model_bucket"]
    ordered = [k for k in preferred if k in keys]
    ordered.extend(k for k in keys if k not in ordered)

    header_labels = {
        "provider": "提供商",
        "model_bucket": "模型分桶",
        "n": "条数",
        "validation_ok": "验证通过",
        "test_ok": "测试通过",
        "validation_rate": "验证率",
        "test_rate": "测试率",
        "avg_generation_time_s": "平均生成耗时(s)",
        "avg_attempts": "平均尝试次数",
    }

    heads = [header_labels.get(k, k) for k in ordered]
    lines.append("| " + " | ".join(heads) + " |")
    lines.append("| " + " | ".join("---" for _ in ordered) + " |")
    for r in rows:
        lines.append("| " + " | ".join(_cell_md(r.get(k)) for k in ordered) + " |")
    return "\n".join(lines)


def _format_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    if not rows:
        buf.write("provider,n,validation_rate,test_rate,avg_generation_time_s,avg_attempts\n")
        return buf.getvalue()
    headers = list(rows[0].keys())
    buf.write(",".join(headers) + "\n")
    for r in rows:
        buf.write(",".join(str(r.get(h, "")) for h in headers) + "\n")
    return buf.getvalue()


def _print_csv(rows: List[Dict[str, Any]]) -> None:
    print(_format_csv(rows), end="")


def main() -> None:
    p = argparse.ArgumentParser(description="从 code_history.db 汇总 LLM 历史表现")
    p.add_argument(
        "--db",
        type=Path,
        default=ROOT / "code_history.db",
        help="SQLite 路径（默认项目根目录 code_history.db）",
    )
    p.add_argument(
        "--by-model",
        action="store_true",
        help="按 extra_data 内 model 再分桶（无则记为 __default__）",
    )
    p.add_argument(
        "--format",
        choices=("json", "csv", "md", "paper"),
        default="paper",
        help="默认 paper=8 列 LLM 论文表；md=按 provider 历史聚合简表；"
        "json 写入 -o 时会同时生成同名 .md；csv 见文档",
    )
    p.add_argument(
        "--with-legend",
        action="store_true",
        help="仅 paper：在表前输出指标说明与数据来源（默认仅输出表格本体）",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config.yaml",
        help="--format paper 时用于提示长度、provider 显示名及全网格槽位枚举",
    )
    p.add_argument(
        "--rates-scope",
        choices=("config_grid", "recorded_slots"),
        default="config_grid",
        help="GSR/VPR/FTPR：config_grid=以 config 全网格为分母（未覆盖槽记 0%%）；"
        "recorded_slots=仅在明细中出现过的槽上平均（样本极少时易显得「全过」）",
    )
    p.add_argument(
        "--performance-json",
        type=Path,
        default=None,
        help="llm_performance.json；省略时优先 experiments/results/，其次仓库根；相对路径落在 experiments/results/",
    )
    p.add_argument(
        "--provider-order",
        nargs="*",
        default=None,
        help="paper 表 LLM 行顺序；默认与论文示例一致",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="写出到文件；缺省打印到 stdout",
    )
    p.add_argument(
        "--no-figure",
        action="store_true",
        help="--format paper 时不生成 2×2 性能图（默认写入 experiments/results/*.pdf）",
    )
    p.add_argument(
        "--figure",
        type=Path,
        default=None,
        help="性能图输出路径；省略则写入 "
        f"{DEFAULT_LLM_PERFORMANCE_FIGURE.relative_to(ROOT)}（PDF）。扩展名决定格式。",
    )
    args = p.parse_args()

    print(
        "[aicrypto-helper] 本脚本只读 SQLite / llm_performance.json，**不会调用 LLM**。",
        file=sys.stderr,
    )
    if args.format != "paper" and (args.figure is not None or args.no_figure):
        print(
            "[aicrypto-helper] 警告: `--figure` / `--no-figure` 仅在 `--format paper`（默认）时生效；"
            f"当前 format=`{args.format}`，已忽略图表相关选项。",
            file=sys.stderr,
        )

    if not args.db.is_file():
        print(f"错误: 找不到数据库 {args.db}", file=sys.stderr)
        sys.exit(1)

    if args.format == "paper":
        perf_path = args.performance_json
        if perf_path is None:
            perf_path = _default_llm_performance_json_path()
        else:
            perf_path = resolve_under_results(Path(perf_path))
        if args.rates_scope == "config_grid":
            n_slots = len(build_reference_slot_keys(ConfigLoader(str(args.config))))
            print(
                "[aicrypto-helper] GSR/VPR/FTPR 按 config.yaml 全网格计分母（未覆盖槽视为 0%）；"
                f"当前网格共 {n_slots} 槽。",
                file=sys.stderr,
            )
        packed = collect_paper_llm_metrics(
            args.db,
            args.config,
            perf_path,
            args.provider_order,
            rates_scope=args.rates_scope,
        )
        text = render_paper_llm_table(
            args.db,
            args.config,
            perf_path,
            args.provider_order,
            table_only=not args.with_legend,
            precomputed=packed,
            rates_scope=args.rates_scope,
        )
        if args.output:
            outp = resolve_under_results(Path(args.output))
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(text, encoding="utf-8")
            print(f"已写入 {outp}")
        else:
            print(text)

        figure_path: Optional[Path] = None
        if not args.no_figure:
            figure_path = (
                resolve_under_results(Path(args.figure))
                if args.figure is not None
                else DEFAULT_LLM_PERFORMANCE_FIGURE
            )

        if figure_path is not None:
            figure_path.parent.mkdir(parents=True, exist_ok=True)
            suf = figure_path.suffix.lower()
            fmt_hint = "PDF 矢量图" if suf == ".pdf" else (
                "PNG 栅格图" if suf in (".png", ".jpg", ".jpeg", ".webp") else "由扩展名决定的图像格式"
            )
            print(
                f"[aicrypto-helper] 正在写入 2×2 性能概览图（{fmt_hint}）→ {figure_path}",
                file=sys.stderr,
            )
            try:
                save_llm_performance_figure(packed[0], figure_path)
                print(
                    f"[aicrypto-helper] 图表已保存: {figure_path.resolve()}",
                    file=sys.stderr,
                )
                print(f"已写入图表 {figure_path}")
            except Exception as e:
                print(f"绘图失败: {e}", file=sys.stderr)
                sys.exit(2)
        else:
            print(
                "[aicrypto-helper] 已跳过图表（`--no-figure`）。",
                file=sys.stderr,
            )
        return

    rows, meta = aggregate(args.db, by_model=args.by_model)
    payload = {"meta": meta, "rows": rows}

    md_agg = _render_markdown_table(rows, meta, args.by_model)
    js_agg = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        outp = resolve_under_results(Path(args.output))
        outp.parent.mkdir(parents=True, exist_ok=True)
        if args.format == "json":
            outp.write_text(js_agg, encoding="utf-8")
            md_path = outp.with_suffix(".md")
            md_path.write_text(md_agg, encoding="utf-8")
            print(f"已写入 JSON: {outp}")
            print(f"已写入 Markdown（论文风格聚合表）: {md_path}")
        elif args.format == "csv":
            outp.write_text(_format_csv(rows), encoding="utf-8")
            print(f"已写入 {outp}")
        else:
            outp.write_text(md_agg, encoding="utf-8")
            print(f"已写入 {outp}")
        return

    if args.format == "json":
        print(md_agg)
        print(
            "\n*（上方为 Markdown 表；若需仅保存 JSON，请使用 `-o out.json`"
            "，将同时生成 `out.md`。）*",
            file=sys.stderr,
        )
        return

    if args.format == "csv":
        _print_csv(rows)
    else:
        print(md_agg)


if __name__ == "__main__":
    main()
