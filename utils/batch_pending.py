"""
批量脚本：筛出仍需调用 LLM 的槽位（与 ``web.server._batch_generate_single`` 跳过逻辑一致）。

保留：**无自 ``since`` 起成功落库** 或 **有成功记录但标准向量复测未通过** 的格。
排除：**同名 provider**（本地类为任一本机线路）成功且复测通过 的格。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from agent.code_saver import rerun_vector_tests_on_code
from agent.crypto_agent import CryptoAgent
from utils.config_loader import ConfigLoader
from utils.history_manager import HistoryManager


def batch_skip_enabled(cfg: ConfigLoader) -> bool:
    try:
        v = cfg.get("local_batch_skip_enabled", True)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        return True


def batch_skip_since(cfg: ConfigLoader) -> str:
    try:
        v = cfg.get("local_batch_skip_if_success_since", "2026-05-04")
        if v is None:
            return "2026-05-04"
        s = str(v).strip()
        if not s or s.lower() in ("null", "none"):
            return "2026-05-04"
        return s[:10] if len(s) >= 10 else s
    except Exception:
        return "2026-05-04"


def provider_is_local_batch(provider: str) -> bool:
    p = (provider or "").lower()
    return "local" in p or "ollama" in p


async def filter_configs_need_llm(
    provider: str,
    configs: List[Dict[str, Any]],
    cfg: ConfigLoader,
    *,
    db_path: Optional[str] = None,
    agent: Optional[CryptoAgent] = None,
    since: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    返回 ``(待调用 LLM 的 config 列表, 统计)``。

    若 ``local_batch_skip_enabled`` 为 false，原样返回全部 configs。
    """
    stats = {
        "input": len(configs),
        "pending": 0,
        "no_record": 0,
        "retest_fail": 0,
        "retest_pass_skip": 0,
        "skip_disabled": 0,
    }
    if not configs:
        return [], stats

    if not batch_skip_enabled(cfg):
        stats["skip_disabled"] = len(configs)
        stats["pending"] = len(configs)
        return list(configs), stats

    since_use = (since or "").strip()[:10] or batch_skip_since(cfg)
    hm = HistoryManager(db_path=db_path) if db_path else HistoryManager()

    own_agent = agent is None
    if own_agent:
        agent = CryptoAgent(provider=provider, enable_validation=False)

    pending: List[Dict[str, Any]] = []
    local_sem = asyncio.Semaphore(
        max(1, min(8, int(cfg.get("local_batch_concurrency", 1) or 1)))
    )

    for conf in configs:
        if provider_is_local_batch(provider):
            rec = hm.get_latest_local_success_record_since(
                conf["algorithm"],
                conf.get("mode"),
                conf["language"],
                since_use,
            )
        else:
            rec = hm.get_latest_success_record_since_for_provider(
                conf["algorithm"],
                conf.get("mode"),
                conf["language"],
                since_use,
                provider,
            )
        hist_code = rec.get("code") if rec else None
        if not isinstance(hist_code, str) or not hist_code.strip():
            pending.append(conf)
            stats["no_record"] += 1
            continue

        hist_op = (rec.get("operation") or "加密解密") if rec else "加密解密"

        async def _retest() -> Tuple[Any, Any]:
            assert agent is not None
            return await rerun_vector_tests_on_code(
                agent,
                hist_code,
                conf["algorithm"],
                conf.get("mode"),
                conf["language"],
                operation=str(hist_op),
            )

        if provider_is_local_batch(provider):
            async with local_sem:
                tr, otr = await _retest()
        else:
            tr, otr = await _retest()

        ok_retest = tr is not None and tr[0]
        if ok_retest:
            stats["retest_pass_skip"] += 1
            if rec and rec.get("id"):
                hm.refresh_history_timestamp(str(rec["id"]))
        else:
            pending.append(conf)
            stats["retest_fail"] += 1

    stats["pending"] = len(pending)
    return pending, stats
