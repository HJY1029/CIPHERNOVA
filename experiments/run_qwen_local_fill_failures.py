#!/usr/bin/env python3
"""
枚举「算法×模式×语言」全网格，减去历史中本地线路已成功用例，对剩余项调用
``web.server._batch_generate_single``（与 Web 批量页 / ``scripts/run_full_llm_matrix.py`` 完全一致：
``enable_validation=False``、``validate=False``、``max_retries=3``、历史复测跳过等）。

默认 provider=qwen_coder_local。用于补跑 Qwen 本地尚未通过的组合；结果追加写入 JSONL。

用法（仓库根目录）：
  python experiments/run_qwen_local_fill_failures.py --dry-run
  python experiments/run_qwen_local_fill_failures.py --limit 3
  python experiments/run_qwen_local_fill_failures.py --ignore-history --limit 1

失败明细默认另存 ``experiments/results/qwen_batch_errors_<UTC>.txt``（与 JSONL 独立）；``--no-errors-log`` 可关闭。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.experiment_outputs import resolve_under_results  # noqa: E402
from utils.batch_error_log import (  # noqa: E402
    append_batch_failure,
    default_qwen_errors_path,
    write_batch_error_log_header,
)
from utils.config_loader import ConfigLoader  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402


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


def _local_batch_skip_since(cfg: ConfigLoader) -> str:
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


def _local_batch_skip_enabled(cfg: ConfigLoader) -> bool:
    try:
        v = cfg.get("local_batch_skip_enabled", True)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        return True


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


def case_key(config: Dict[str, Any]) -> Tuple[str, str, str]:
    return HistoryManager.normalize_case_key(
        config.get("algorithm"),
        config.get("mode"),
        config.get("language"),
    )


def suggest_prompt_refs(config: Dict[str, Any]) -> Dict[str, Any]:
    """仅作排障提示：常见 prompt 路径惯例（非强制）。"""
    alg = (config.get("algorithm") or "").strip().upper()
    mode = (config.get("mode") or "").strip().upper()
    lang = (config.get("language") or "python").strip().lower()
    refs: Dict[str, Any] = {
        "llm_bootstrap": "prompts/llms/qwen_coder_local/llm.yaml",
    }
    if mode:
        refs["algorithm_stub"] = f"prompts/algorithms/{lang}/{alg}-{mode}.yaml"
    else:
        refs["algorithm_stub"] = f"prompts/algorithms/{lang}/{alg}.yaml"
    return refs


async def run_one(
    provider: str,
    config: Dict[str, Any],
    sem: asyncio.Semaphore,
    *,
    batch_one: Any,
    gen_out_dir: Path,
) -> Dict[str, Any]:
    """与 ``web.server._batch_generate_single(provider, config)`` 一致（agent=None）。"""
    t0 = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - t0) * 1000)

    try:
        if _provider_is_local_batch(provider):
            async with sem:
                r = await batch_one(provider, config)
        else:
            r = await batch_one(provider, config)
        out: Dict[str, Any] = {
            "config": config,
            "success": bool(r.get("success")),
            "error": r.get("error"),
            "generation_ok": r.get("generation_ok"),
            "total_ms": elapsed_ms(),
            "vector_status": r.get("vector_status"),
            "vector_detail": r.get("vector_detail"),
            "case_id": r.get("case_id"),
            "code": r.get("code"),
            "filename": r.get("filename"),
            "generated_code": r.get("generated_code"),
            "generated_filename": r.get("generated_filename"),
        }
        if r.get("success") and r.get("filename"):
            out["filepath"] = str(gen_out_dir / str(r["filename"]).strip())
        if not r.get("success"):
            out["prompt_refs"] = suggest_prompt_refs(config)
        return out
    except Exception as e:
        return {
            "config": config,
            "success": False,
            "error": str(e),
            "generation_ok": False,
            "total_ms": elapsed_ms(),
            "prompt_refs": suggest_prompt_refs(config),
        }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="本地 Qwen 补跑：全网格 − 历史已成功")
    p.add_argument("--config", default="config.yaml", help="配置文件路径")
    p.add_argument("--provider", default="qwen_coder_local", help="LLM provider 名")
    p.add_argument("--dry-run", action="store_true", help="只列出待跑项，不调用模型")
    p.add_argument(
        "--ignore-history",
        action="store_true",
        help="不把历史成功从待跑列表中减去（仍写入新结果）",
    )
    p.add_argument("--since", default=None, help="覆盖 config 中 local_batch_skip_if_success_since（YYYY-MM-DD）")
    p.add_argument("--limit", type=int, default=0, help="最多跑几条（0=不限制）")
    p.add_argument(
        "--output-jsonl",
        default="qwen_local_fill_failures_runs.jsonl",
        help="每条结果追加写入的 JSONL（相对路径默认在 experiments/results/；绝对路径不变）",
    )
    p.add_argument(
        "--errors-log",
        type=Path,
        default=None,
        help="失败明细文本；省略则默认 experiments/results/qwen_batch_errors_<utc>.txt",
    )
    p.add_argument(
        "--no-errors-log",
        action="store_true",
        help="不写失败明细文本文件",
    )
    return p.parse_args()


async def async_main() -> int:
    args = parse_args()
    cfg_path = ROOT / args.config if not Path(args.config).is_absolute() else Path(args.config)
    cfg = ConfigLoader(str(cfg_path))

    since = (args.since or "").strip()[:10] if args.since else _local_batch_skip_since(cfg)
    grid = build_full_grid(cfg)
    all_keys: Set[Tuple[str, str, str]] = {case_key(c) for c in grid}

    skip_ok = _local_batch_skip_enabled(cfg) and not args.ignore_history
    success_keys: Set[Tuple[str, str, str]] = set()
    if skip_ok:
        success_keys = HistoryManager(
            db_path=str(ROOT / "code_history.db")
        ).get_local_success_case_keys_since(since)

    pending: List[Dict[str, Any]] = []
    for c in grid:
        k = case_key(c)
        if k not in success_keys:
            pending.append(c)

    print(f"全网格: {len(grid)} 条；唯一键: {len(all_keys)}；自 {since} 起本地已成功: {len(success_keys)}；待跑: {len(pending)}")
    if args.dry_run:
        for i, c in enumerate(pending[:20]):
            print(f"  [{i}] {c}")
        if len(pending) > 20:
            print(f"  ... 共 {len(pending)} 条，仅显示前 20")
        return 0

    sem = asyncio.Semaphore(_local_batch_concurrency(cfg))
    batch_one = load_batch_generate_single()
    _od = Path(str(cfg.get("output_dir") or "./generated_code").strip())
    gen_out_dir = _od.resolve() if _od.is_absolute() else (ROOT / _od).resolve()
    out_path = resolve_under_results(Path(args.output_jsonl))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    err_fp = None
    err_log_path = None
    if not args.no_errors_log:
        err_log_path = (
            resolve_under_results(Path(args.errors_log))
            if args.errors_log
            else default_qwen_errors_path(ROOT)
        )
        err_log_path.parent.mkdir(parents=True, exist_ok=True)
        err_fp = open(err_log_path, "w", encoding="utf-8")
        write_batch_error_log_header(
            err_fp,
            title="experiments/run_qwen_local_fill_failures.py — Qwen 批量失败记录",
            extra_lines=[
                f"provider: {args.provider}",
                f"jsonl: {out_path}",
                f"since: {since}",
                f"pending_count: {len(pending)}",
            ],
        )

    n_run = 0
    for c in pending:
        if args.limit and n_run >= args.limit:
            break
        rec = {
            "ts": datetime.now().isoformat(),
            "provider": args.provider,
            "since_used": since,
            "skip_history": skip_ok,
            **(await run_one(args.provider, c, sem, batch_one=batch_one, gen_out_dir=gen_out_dir)),
        }
        n_run += 1
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if err_fp is not None:
            inner = {k: v for k, v in rec.items() if k not in ("ts", "provider", "since_used", "skip_history")}
            append_batch_failure(
                err_fp,
                provider=str(rec.get("provider") or args.provider),
                config=c,
                result=inner,
            )
        label = f"{c.get('algorithm')} {c.get('mode') or ''} {c.get('language')}".strip()
        ok = rec.get("success")
        err = rec.get("error")
        print(f"[{n_run}] {label} -> {'OK' if ok else 'FAIL'} {err or ''}")

    if err_fp is not None:
        err_fp.close()
        print(f"失败明细（供改进 prompt）: {err_log_path}")
    print(f"完成 {n_run} 条，日志: {out_path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
