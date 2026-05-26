"""
Qwen 本地按算法拆分批量生成（与 ``web.server._batch_generate_single`` 同源）。

默认仅跑「未落库」或「历史复测未通过」的格（``utils.batch_pending``，与 Web 跳过逻辑一致）；
跑满网格请加 ``--all-slots``。

供 ``run_qwen_batch_des.py`` / ``aes`` / ``rsa`` / ``sm4`` 调用；勿直接当 CLI 使用。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.experiment_outputs import experiments_results_dir, resolve_under_results  # noqa: E402
from utils.batch_error_log import append_batch_failure, write_batch_error_log_header  # noqa: E402
from utils.batch_pending import batch_skip_since, filter_configs_need_llm  # noqa: E402
from utils.config_loader import ConfigLoader  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402

SUPPORTED_ALGORITHMS = ("DES", "AES", "RSA", "SM4")

# 与 experiments/_slot_fail_analysis、qwen_history_retest 对齐；统计变化后可更新
# 2026-05-23：批量 7/8 成功（AES-GCM-cpp、7×无DB 均已落库/复测通过）；仅剩 SM4-OFB-cpp
QWEN_FAILING_SLOT_KEYS: Tuple[Tuple[str, str, str], ...] = (
    ("SM4", "OFB", "cpp"),   # vpr/ftpr（误用 EVP_sm4_ofb128、缺 string.h、iostream）
)

# code_history 无 qwen_coder_local test_success=1 → 强制重跑落库（当前无）
QWEN_FORCE_REGEN_NO_DB_KEYS: Tuple[Tuple[str, str, str], ...] = ()


def _all_qwen_batch_slot_keys() -> Tuple[Tuple[str, str, str], ...]:
    """未过槽 + 无 DB 成功槽（去重，保持顺序）。"""
    seen: set = set()
    out: List[Tuple[str, str, str]] = []
    for item in QWEN_FAILING_SLOT_KEYS + QWEN_FORCE_REGEN_NO_DB_KEYS:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def build_qwen_failing_slot_configs() -> List[Dict[str, Any]]:
    """Qwen 批量重跑槽：未过 + 仅 JSON 通过无 DB 落库（见 ``_all_qwen_batch_slot_keys``）。"""
    return [
        {"algorithm": alg, "mode": mode, "language": lang}
        for alg, mode, lang in _all_qwen_batch_slot_keys()
    ]


def is_force_regen_no_db_slot(config: Dict[str, Any]) -> bool:
    """是否属于「仅 performance JSON 通过、须强制 qwen 落库」槽。"""
    sk = case_key(config)
    force = {
        HistoryManager.normalize_case_key(alg, mode, lang)
        for alg, mode, lang in QWEN_FORCE_REGEN_NO_DB_KEYS
    }
    return sk in force


def load_batch_generate_single():
    spec = importlib.util.spec_from_file_location("ciphernova_web_server", ROOT / "web" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod._batch_generate_single


def _local_batch_concurrency(cfg: ConfigLoader) -> int:
    try:
        n = int(cfg.get("local_batch_concurrency", 1) or 1)
        return max(1, min(8, n))
    except Exception:
        return 1


def _provider_is_local_batch(provider: str) -> bool:
    p = (provider or "").lower()
    return "local" in p or "ollama" in p


def qwen_batch_generate_kwargs(provider: str, *, failing_slots: bool = False) -> Dict[str, Any]:
    """Qwen 批量生成传给 ``generate_and_save`` 的 kwargs。

    ``failing_slots=True``：当前仅剩 SM4-OFB-cpp 等难槽，**跳过蒸馏**（避免 Python/AES 串题），
    依赖算法 YAML 骨架 + 测试反馈改进。
    """
    p = (provider or "").strip().lower()
    if failing_slots and (p == "qwen_coder_local" or p.startswith("qwen_coder_local_")):
        return {"_skip_distillation": True}
    return {}


def build_full_grid(cfg: ConfigLoader) -> List[Dict[str, Any]]:
    langs = list(cfg.get("supported_languages") or ["python", "c", "cpp"])
    algs = list(cfg.get("crypto_algorithms") or ["DES", "AES", "RSA", "SM4"])
    des_modes = list(cfg.get("des_modes") or ["ECB", "CBC", "CFB", "OFB"])
    aes_modes = list(cfg.get("aes_modes") or [])
    sm4_modes = list(cfg.get("sm4_modes") or des_modes)

    out: List[Dict[str, Any]] = []
    for alg in algs:
        u = (alg or "").strip().upper()
        if u == "RSA":
            for lang in langs:
                out.append({"algorithm": u, "language": lang})
        elif u == "AES":
            for mode in aes_modes:
                for lang in langs:
                    out.append({"algorithm": u, "mode": mode, "language": lang})
        elif u in ("DES", "SM4"):
            modes = des_modes if u == "DES" else sm4_modes
            for mode in modes:
                for lang in langs:
                    out.append({"algorithm": u, "mode": mode, "language": lang})
    return out


def build_grid_for_algorithm(cfg: ConfigLoader, algorithm: str) -> List[Dict[str, Any]]:
    want = (algorithm or "").strip().upper()
    if want not in SUPPORTED_ALGORITHMS:
        raise ValueError(f"不支持的算法: {algorithm!r}，须为 {SUPPORTED_ALGORITHMS}")
    return [c for c in build_full_grid(cfg) if (c.get("algorithm") or "").upper() == want]


def case_key(config: Dict[str, Any]) -> Tuple[str, str, str]:
    return HistoryManager.normalize_case_key(
        config.get("algorithm"),
        config.get("mode"),
        config.get("language"),
    )


def _default_errors_path(algorithm: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return experiments_results_dir() / f"qwen_batch_{algorithm.upper()}_errors_{ts}.txt"


def _default_out_path(algorithm: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return experiments_results_dir() / f"qwen_batch_{algorithm.upper()}_{ts}.json"


def _default_failing_out_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return experiments_results_dir() / f"qwen_batch_failing_slots_{ts}.json"


def _default_failing_errors_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return experiments_results_dir() / f"qwen_batch_failing_slots_errors_{ts}.txt"


def _build_arg_parser(algorithm: str) -> argparse.ArgumentParser:
    alg = algorithm.upper()
    p = argparse.ArgumentParser(
        description=f"Qwen 本地批量：仅 {alg}（调用 web.server._batch_generate_single，与 Web 批量页一致）",
    )
    p.add_argument("--config", default="config.yaml", help="配置文件路径（相对仓库根）")
    p.add_argument("--provider", default="qwen_coder_local", help="LLM provider（默认 qwen_coder_local）")
    p.add_argument("--dry-run", action="store_true", help="只打印本算法网格条数与示例，不调用模型")
    p.add_argument(
        "--all-slots",
        action="store_true",
        help="跑满本算法网格（默认仅「未落库」或「历史复测未通过」的格才调 LLM）",
    )
    p.add_argument(
        "--only-pending",
        action="store_true",
        help="显式仅跑待生成格（与默认行为相同；与 --all-slots 互斥）",
    )
    p.add_argument("--since", default=None, help="覆盖 config 中 local_batch_skip_if_success_since（YYYY-MM-DD）")
    p.add_argument("--offset", type=int, default=0, help="跳过本算法网格前 N 条")
    p.add_argument("--limit", type=int, default=None, help="最多执行 N 条（应用 offset 之后）")
    p.add_argument("--out", type=Path, default=None, help=f"结果 JSON（默认 experiments/results/qwen_batch_{alg}_<utc>.json）")
    p.add_argument("--errors-log", type=Path, default=None, help="失败明细文本路径")
    p.add_argument("--no-errors-log", action="store_true", help="不写失败明细")
    return p


async def async_main_for_algorithm(algorithm: str) -> int:
    alg = (algorithm or "").strip().upper()
    if alg not in SUPPORTED_ALGORITHMS:
        print(f"错误: 算法须为 {SUPPORTED_ALGORITHMS}，收到 {algorithm!r}", file=sys.stderr)
        return 2

    args = _build_arg_parser(alg).parse_args()
    cfg_path = ROOT / args.config if not Path(args.config).is_absolute() else Path(args.config)
    cfg = ConfigLoader(str(cfg_path))
    provider = (args.provider or "qwen_coder_local").strip()

    since = (args.since or "").strip()[:10] if args.since else batch_skip_since(cfg)
    configs = build_grid_for_algorithm(cfg, alg)
    only_pending = not args.all_slots

    if args.dry_run:
        print(
            f"[{alg}] provider={provider} 算法网格 {len(configs)} 条"
            + ("（实际运行默认仅待生成格，会先做历史复测预筛）" if only_pending else "（--all-slots 跑满网格）")
        )
        for i, c in enumerate(configs[:8]):
            print(f"  [{i}] {c}")
        if len(configs) > 8:
            print(f"  ... 共 {len(configs)} 条")
        return 0

    pending_stats: Dict[str, int] = {}
    if only_pending:
        before = len(configs)
        configs, pending_stats = await filter_configs_need_llm(
            provider,
            configs,
            cfg,
            db_path=str(ROOT / "code_history.db"),
            since=since,
        )
        print(
            f"[{alg}] 仅待生成（since={since}，provider={provider}）："
            f"网格 {before} → 待执行 {len(configs)} "
            f"(无成功落库 {pending_stats.get('no_record', 0)}，"
            f"复测未过 {pending_stats.get('retest_fail', 0)}，"
            f"复测通过跳过 {pending_stats.get('retest_pass_skip', 0)})"
        )

    if args.offset:
        if args.offset >= len(configs):
            print(f"错误: --offset {args.offset} 不小于本算法网格长度 {len(configs)}", file=sys.stderr)
            return 2
        configs = configs[args.offset :]
    if args.limit is not None:
        if args.limit <= 0:
            print("错误: --limit 须为正整数", file=sys.stderr)
            return 2
        configs = configs[: args.limit]

    print(f"[{alg}] provider={provider} 待执行 {len(configs)} 条（与 Web 批量：validate=False, max_retries=3）")

    if not configs:
        print(f"[{alg}] 无待执行条目，退出。")
        return 0

    batch_one = load_batch_generate_single()
    sem = asyncio.Semaphore(_local_batch_concurrency(cfg))
    out_path = resolve_under_results(args.out) if args.out else _default_out_path(alg)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    err_fp = None
    err_log_path: Optional[Path] = None
    if not args.no_errors_log:
        err_log_path = (
            resolve_under_results(Path(args.errors_log))
            if args.errors_log
            else _default_errors_path(alg)
        )
        err_log_path.parent.mkdir(parents=True, exist_ok=True)
        err_fp = open(err_log_path, "w", encoding="utf-8")
        write_batch_error_log_header(
            err_fp,
            title=f"scripts/run_qwen_batch_{alg.lower()}.py — Qwen 批量失败（仅 {alg}）",
            extra_lines=[
                f"provider: {provider}",
                f"results_json: {out_path}",
                f"configs_count: {len(configs)}",
                f"since: {since}",
                f"only_pending: {only_pending}",
                f"pending_stats: {pending_stats}",
            ],
        )

    cases: List[Dict[str, Any]] = []
    for j, conf in enumerate(configs):
        label = f"{conf.get('algorithm')}-{conf.get('mode', '')}-{conf['language']}".strip("-")
        print(f"[{alg}] ({j + 1}/{len(configs)}) {label} ...", flush=True)
        t0 = time.perf_counter()
        try:
            if _provider_is_local_batch(provider):
                async with sem:
                    r = await batch_one(provider, conf)
            else:
                r = await batch_one(provider, conf)
        except Exception as e:
            r = {
                "success": False,
                "error": str(e),
                "generation_ok": False,
                "vector_status": None,
                "vector_detail": None,
                "total_ms": int((time.perf_counter() - t0) * 1000),
                "case_id": None,
            }
        slim = {
            "config": conf,
            "success": r.get("success"),
            "error": r.get("error"),
            "vector_status": r.get("vector_status"),
            "vector_detail": r.get("vector_detail"),
            "total_ms": r.get("total_ms"),
            "case_id": r.get("case_id"),
            "generation_ok": r.get("generation_ok"),
        }
        cases.append(slim)
        if err_fp is not None:
            append_batch_failure(err_fp, provider=provider, config=conf, result=r)
        st = "OK" if r.get("success") else "FAIL"
        print(f"    -> {st} {r.get('total_ms')}ms", flush=True)

    report = {
        "algorithm": alg,
        "provider": provider,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "configs_count": len(configs),
        "since_used": since,
        "only_pending": only_pending,
        "pending_filter_stats": pending_stats,
        "web_batch": True,
        "cases": cases,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_n = sum(1 for c in cases if c.get("success"))
    print(f"[{alg}] 完成 {ok_n}/{len(cases)} 成功，JSON: {out_path}")
    if err_fp is not None:
        err_fp.close()
        print(f"[{alg}] 失败明细: {err_log_path}")
    return 0 if ok_n == len(cases) else 1


def main_for_algorithm(algorithm: str) -> int:
    return asyncio.run(async_main_for_algorithm(algorithm))


def _build_failing_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Qwen 本地批量：未过槽 + 无 DB 落库须重跑（见 qwen_batch_common 槽位表）",
    )
    p.add_argument("--config", default="config.yaml", help="配置文件路径（相对仓库根）")
    p.add_argument("--provider", default="qwen_coder_local", help="LLM provider")
    n_slots = len(_all_qwen_batch_slot_keys())
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=f"只打印 {n_slots} 槽列表与预筛结果，不调模型",
    )
    p.add_argument(
        "--all-slots",
        action="store_true",
        help=f"同 --force-all：{n_slots} 槽全部调 LLM，不做历史复测预筛",
    )
    p.add_argument(
        "--force-all",
        action="store_true",
        help=f"强制 {n_slots} 槽全部调 LLM（不做历史复测预筛）",
    )
    p.add_argument("--since", default=None, help="覆盖 local_batch_skip_if_success_since")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--errors-log", type=Path, default=None)
    p.add_argument("--no-errors-log", action="store_true")
    return p


async def async_main_failing_slots() -> int:
    args = _build_failing_arg_parser().parse_args()
    cfg_path = ROOT / args.config if not Path(args.config).is_absolute() else Path(args.config)
    cfg = ConfigLoader(str(cfg_path))
    provider = (args.provider or "qwen_coder_local").strip()
    since = (args.since or "").strip()[:10] if args.since else batch_skip_since(cfg)

    configs = build_qwen_failing_slot_configs()
    only_pending = not (args.all_slots or args.force_all)
    n_fixed = len(_all_qwen_batch_slot_keys())
    n_force = len(QWEN_FORCE_REGEN_NO_DB_KEYS)
    log_tag = "FAILING"

    if args.dry_run:
        print(
            f"[{log_tag}] provider={provider} 批量槽 {len(configs)}/{n_fixed} 条 "
            f"(未过 {len(QWEN_FAILING_SLOT_KEYS)} + 无DB强制重跑 {n_force})"
        )
        for i, c in enumerate(configs):
            print(f"  [{i + 1}] {c}")
        if only_pending:
            pending, st = await filter_configs_need_llm(
                provider,
                configs,
                cfg,
                db_path=str(ROOT / "code_history.db"),
                since=since,
            )
            print(
                f"[{log_tag}] 预筛后待执行 {len(pending)}/{len(configs)} "
                f"(无落库 {st.get('no_record', 0)}，复测未过 {st.get('retest_fail', 0)}，"
                f"复测通过跳过 {st.get('retest_pass_skip', 0)})"
            )
        return 0

    pending_stats: Dict[str, int] = {}
    if only_pending:
        before = len(configs)
        force_cfgs = [c for c in configs if is_force_regen_no_db_slot(c)]
        rest_cfgs = [c for c in configs if not is_force_regen_no_db_slot(c)]
        pending_rest, pending_stats = await filter_configs_need_llm(
            provider,
            rest_cfgs,
            cfg,
            db_path=str(ROOT / "code_history.db"),
            since=since,
        )
        # 无 qwen DB 成功：即使 JSON/其它 local 复测通过也强制调 LLM 落库
        configs = force_cfgs + pending_rest
        pending_stats["force_regen_no_db"] = len(force_cfgs)
        print(
            f"[{log_tag}] 仅待生成（since={since}）："
            f"列表 {before}/{n_fixed} → 待执行 {len(configs)} "
            f"(无DB强制重跑 {len(force_cfgs)}，"
            f"其余预筛后 {len(pending_rest)}；"
            f"无落库 {pending_stats.get('no_record', 0)}，"
            f"复测未过 {pending_stats.get('retest_fail', 0)}，"
            f"复测通过跳过 {pending_stats.get('retest_pass_skip', 0)})"
        )
    else:
        print(f"[{log_tag}] 强制跑满 {n_fixed} 槽（--force-all 或 --all-slots 且未预筛）")

    if args.offset:
        if args.offset >= len(configs):
            print(f"错误: --offset {args.offset} >= {len(configs)}", file=sys.stderr)
            return 2
        configs = configs[args.offset :]
    if args.limit is not None:
        if args.limit <= 0:
            print("错误: --limit 须为正整数", file=sys.stderr)
            return 2
        configs = configs[: args.limit]

    gen_kw = qwen_batch_generate_kwargs(provider, failing_slots=True)
    print(
        f"[{log_tag}] provider={provider} 待执行 {len(configs)} 条"
        + ("（跳过蒸馏 _skip_distillation）" if gen_kw.get("_skip_distillation") else "")
    )

    if not configs:
        print(f"[{log_tag}] 无待执行条目（可能 {n_fixed} 槽均已复测通过），退出。")
        return 0

    batch_one = load_batch_generate_single()
    sem = asyncio.Semaphore(_local_batch_concurrency(cfg))
    out_path = resolve_under_results(args.out) if args.out else _default_failing_out_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    err_fp = None
    err_log_path: Optional[Path] = None
    if not args.no_errors_log:
        err_log_path = (
            resolve_under_results(Path(args.errors_log))
            if args.errors_log
            else _default_failing_errors_path()
        )
        err_log_path.parent.mkdir(parents=True, exist_ok=True)
        err_fp = open(err_log_path, "w", encoding="utf-8")
        write_batch_error_log_header(
            err_fp,
            title=f"scripts/run_qwen_batch_failing_slots.py — Qwen 未过槽（{n_fixed}）",
            extra_lines=[
                f"provider: {provider}",
                f"results_json: {out_path}",
                f"failing_slots_total: {len(_all_qwen_batch_slot_keys())}",
                f"force_regen_no_db: {len(QWEN_FORCE_REGEN_NO_DB_KEYS)}",
                f"configs_count: {len(configs)}",
                f"since: {since}",
                f"only_pending: {only_pending}",
                f"pending_stats: {pending_stats}",
            ],
        )

    cases: List[Dict[str, Any]] = []
    for j, conf in enumerate(configs):
        label = f"{conf.get('algorithm')}-{conf.get('mode', '')}-{conf['language']}".strip("-")
        print(f"[{log_tag}] ({j + 1}/{len(configs)}) {label} ...", flush=True)
        t0 = time.perf_counter()
        try:
            if _provider_is_local_batch(provider):
                async with sem:
                    r = await batch_one(provider, conf, generate_kwargs=gen_kw)
            else:
                r = await batch_one(provider, conf, generate_kwargs=gen_kw)
        except Exception as e:
            r = {
                "success": False,
                "error": str(e),
                "generation_ok": False,
                "vector_status": None,
                "vector_detail": None,
                "total_ms": int((time.perf_counter() - t0) * 1000),
                "case_id": None,
            }
        slim = {
            "config": conf,
            "success": r.get("success"),
            "error": r.get("error"),
            "vector_status": r.get("vector_status"),
            "vector_detail": r.get("vector_detail"),
            "total_ms": r.get("total_ms"),
            "case_id": r.get("case_id"),
            "generation_ok": r.get("generation_ok"),
        }
        cases.append(slim)
        if err_fp is not None:
            append_batch_failure(err_fp, provider=provider, config=conf, result=r)
        st = "OK" if r.get("success") else "FAIL"
        print(f"    -> {st} {r.get('total_ms')}ms", flush=True)

    report = {
        "batch": "failing_slots",
        "distillation_keep": bool(gen_kw.get("_keep_distillation")),
        "distillation_skip": bool(gen_kw.get("_skip_distillation")),
        "provider": provider,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "failing_slots_total": len(_all_qwen_batch_slot_keys()),
        "force_regen_no_db": len(QWEN_FORCE_REGEN_NO_DB_KEYS),
        "configs_count": len(configs),
        "since_used": since,
        "only_pending": only_pending,
        "pending_filter_stats": pending_stats,
        "web_batch": True,
        "cases": cases,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_n = sum(1 for c in cases if c.get("success"))
    print(f"[{log_tag}] 完成 {ok_n}/{len(cases)} 成功，JSON: {out_path}")
    if err_fp is not None:
        err_fp.close()
        print(f"[{log_tag}] 失败明细: {err_log_path}")
    return 0 if ok_n == len(cases) else 1


def main_failing_slots() -> int:
    return asyncio.run(async_main_failing_slots())
