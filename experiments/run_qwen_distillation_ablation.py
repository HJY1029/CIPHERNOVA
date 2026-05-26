#!/usr/bin/env python3
"""
本地 Qwen（默认 ``qwen_coder_local``）蒸馏前后对比实验。

在同一 **算法×模式×语言** 网格上跑两轮，每格与 **Web 批量单条** 同源调用 ``web.server._batch_generate_single(..., agent=…)``：
1. **无蒸馏**：将 ``config.yaml`` 中 ``distillation.enabled`` 覆盖为 ``False``（不注入教师少样本 / 改进参考）。
2. **有蒸馏**：恢复配置中的蒸馏开关（通常为 ``True``），使用 ``dataset_path`` 指向的 JSONL 教师池。

与 Web 一致：``enable_validation=False``、``validate=False``、``local_batch_skip_enabled`` 时 **有蒸馏**阶段先历史复测再跳过 LLM。

    **无蒸馏基线（Phase 1）**：
    - 传 ``generate_kwargs={"_skip_history_retest": True, "max_retries": 1}``：**强制重新生成**（不沿用历史），且**仅 1 次从头 generate**（不做第 2/3 轮整段重采样）。
    - 仍保留 ``config.yaml`` 中 ``self_refine.max_refine_rounds``（默认 3）轮 **Self-Refine**（测试反馈 improve）。

    **有蒸馏（Phase 2）**：
    - ``max_retries=3``（默认）；``local_batch_skip_enabled`` 时历史复测通过则跳过 LLM。

    **增量更新（保留历史 44 格，只重测指定槽）**::

      python experiments/run_qwen_distillation_ablation.py --invoke \\
        --update-sm4-ofb-python \\
        -o experiments/qwen_distill_compare.md \\
        --json-output experiments/qwen_distill_compare.json

    - 默认从 ``experiments/results/experiments/qwen_distill_compare.json`` 合并先前结果（可用 ``--merge-from`` 覆盖）。
    - **无蒸馏**：仅 **SM4|OFB|python** 强制重跑（``_skip_history_retest`` + ``max_retries=1``）。
    - **有蒸馏**：仅 **SM4|OFB|python** 历史复测（``_history_only``，**不调 LLM**）；其余 44 格保留合并数据。
    - 通用：``--merge-from PATH`` + ``--refresh-cells``；或 ``--refresh-baseline-cells`` / ``--refresh-distill-cells`` 分别指定两阶段。

    与论文表 ``tab:distillation_overall`` 口径一致时，应对 **45 格**（四算法全模式×三语言，RSA 无 mode）统计通过率。
可按算法拆分并行（与 ``scripts/run_qwen_batch_*.py`` 同网格）::

  python experiments/run_qwen_distill_des.py --invoke
  python experiments/run_qwen_distill_aes.py --invoke
  python experiments/run_qwen_distill_rsa.py --invoke
  python experiments/run_qwen_distill_sm4.py --invoke

用法（仓库根目录）::

  python experiments/run_qwen_distillation_ablation.py --dry-run
  python experiments/run_qwen_distillation_ablation.py --algorithm DES --invoke
  python experiments/run_qwen_distillation_ablation.py --invoke --limit 5
  python experiments/run_qwen_distillation_ablation.py --invoke -o qwen_distill_compare.md
    （相对路径写入 experiments/results/。）

**教师池**：开启蒸馏前请确保 ``data/distillation_teacher.jsonl`` 中有足够条目（可由云端 provider 跑通任务后自动追加，见 ``config.yaml`` 的 ``auto_collect_cloud_teachers``）。

默认 **不会** 调用 LLM；须 ``--invoke`` 或 ``--live``。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.crypto_agent import CryptoAgent  # noqa: E402
from experiments.experiment_outputs import resolve_under_results  # noqa: E402
from experiments.experiment_checkpoint import (  # noqa: E402
    atomic_write_json,
    checkpoint_mismatch_message,
    fingerprint_from_payload,
    load_json_optional,
)
from scripts.qwen_batch_common import (  # noqa: E402
    build_full_grid,
    build_grid_for_algorithm,
    load_batch_generate_single,
)
from utils.config_loader import ConfigLoader  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402


def _cell_tuple(cell: Dict[str, Any]) -> Tuple[str, str, str]:
    return HistoryManager.normalize_case_key(
        cell.get("algorithm"), cell.get("mode"), cell.get("language")
    )


def _parse_refresh_keys(
    refresh_cells: List[str], *, update_sm4_ofb_python: bool
) -> Optional[Set[Tuple[str, str, str]]]:
    keys: Set[Tuple[str, str, str]] = set()
    if update_sm4_ofb_python:
        keys.add(("SM4", "ofb", "python"))
    for raw in refresh_cells:
        raw = (raw or "").strip()
        if not raw:
            continue
        parts = re.split(r"[:,|]", raw)
        if len(parts) != 3:
            raise SystemExit(f"--refresh-cells 须为 ALG:MODE:LANG，收到: {raw!r}")
        keys.add(
            (
                parts[0].strip().upper(),
                parts[1].strip().lower(),
                parts[2].strip().lower(),
            )
        )
    return keys if keys else None


def _resolve_refresh_keys(
    *,
    refresh_cells: List[str],
    refresh_baseline_cells: List[str],
    refresh_distill_cells: List[str],
    update_sm4_ofb_python: bool,
) -> Tuple[Optional[Set[Tuple[str, str, str]]], Optional[Set[Tuple[str, str, str]]], bool]:
    """返回 (baseline_keys, distill_keys, partial_update)。"""
    common = _parse_refresh_keys(refresh_cells, update_sm4_ofb_python=update_sm4_ofb_python)
    baseline_only = _parse_refresh_keys(
        refresh_baseline_cells, update_sm4_ofb_python=False
    )
    distill_only = _parse_refresh_keys(
        refresh_distill_cells, update_sm4_ofb_python=False
    )
    if update_sm4_ofb_python and common is None:
        common = {("SM4", "ofb", "python")}
    baseline_keys = baseline_only if baseline_only is not None else common
    distill_keys = distill_only if distill_only is not None else common
    partial = baseline_keys is not None or distill_keys is not None
    return baseline_keys, distill_keys, partial


def _should_refresh_cell(
    cell: Dict[str, Any], refresh_keys: Optional[Set[Tuple[str, str, str]]]
) -> bool:
    if refresh_keys is None:
        return True
    return _cell_tuple(cell) in refresh_keys


def _pad_results(
    lst: Optional[List[Optional[Dict[str, Any]]]], n: int
) -> List[Optional[Dict[str, Any]]]:
    out: List[Optional[Dict[str, Any]]] = list(lst or [])
    while len(out) < n:
        out.append(None)
    return out[:n]


def _load_merge_results(
    grid: List[Dict[str, Any]], path: Path
) -> Tuple[List[Optional[Dict[str, Any]]], List[Optional[Dict[str, Any]]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    by_key_b: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    by_key_d: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for r in data.get("results_baseline") or []:
        c = r.get("cell") or {}
        if c.get("algorithm"):
            by_key_b[_cell_tuple(c)] = r
    for r in data.get("results_distill") or []:
        c = r.get("cell") or {}
        if c.get("algorithm"):
            by_key_d[_cell_tuple(c)] = r
    baseline: List[Optional[Dict[str, Any]]] = []
    distill: List[Optional[Dict[str, Any]]] = []
    for cell in grid:
        k = _cell_tuple(cell)
        baseline.append(by_key_b.get(k))
        distill.append(by_key_d.get(k))
    return baseline, distill


def _count_filled(results: List[Optional[Dict[str, Any]]]) -> int:
    return sum(1 for r in results if r is not None)


def _result_ftpr(entry: Optional[Dict[str, Any]]) -> Optional[bool]:
    if entry is None:
        return None
    return bool(entry.get("ftpr"))


def _apply_distillation_enabled(agent: CryptoAgent, enabled: bool) -> None:
    agent.config._config.setdefault("distillation", {})
    agent.config._config["distillation"]["enabled"] = enabled


def _history_skip_from_batch(r: Dict[str, Any]) -> bool:
    vd = str(r.get("vector_detail") or "")
    return "历史代码复测通过" in vd


def _teacher_pool_stats(cfg: ConfigLoader) -> Tuple[int, Optional[str]]:
    dist = cfg.get("distillation") or {}
    path = (dist.get("dataset_path") or "").strip()
    if not path:
        return 0, None
    p = Path(path)
    if not p.is_file():
        return 0, str(p)
    n = 0
    try:
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                    n += 1
                except json.JSONDecodeError:
                    continue
    except OSError:
        return 0, str(p)
    return n, str(p.resolve())


def _cell_label(c: Dict[str, Any]) -> str:
    alg = c.get("algorithm", "")
    mode = c.get("mode") or ""
    lang = c.get("language", "")
    if (alg or "").upper() == "RSA":
        return f"{alg}|RSA|{lang}"
    return f"{alg}|{mode}|{lang}"


def _outcome_from_web_batch(
    r: Dict[str, Any], agent: CryptoAgent, *, elapsed: float
) -> Dict[str, Any]:
    """将 Web 批量单条返回映射为蒸馏脚本原有结果字段（FTPR=success；VPR 与批量一致不做单独编译列）。"""
    ok = bool(r.get("success"))
    code = (r.get("code") or "").strip()
    gsr = len(code) > 30
    vpr = ok
    ftpr = ok
    filepath_str = ""
    if ok:
        fn = r.get("filename")
        if isinstance(fn, str) and fn.strip():
            p = agent.output_dir / fn.strip()
            if p.is_file():
                filepath_str = str(p)
    err = None if ok else (r.get("error") or r.get("vector_detail") or "失败")
    hist = _history_skip_from_batch(r)
    return {
        "ok": ok,
        "error": err,
        "gsr": gsr,
        "vpr": vpr,
        "ftpr": ftpr,
        "seconds": elapsed,
        "filepath": filepath_str or None,
        "vector_detail": r.get("vector_detail"),
        "generation_ok": r.get("generation_ok"),
        "history_skip": hist,
    }


def _provider_is_local_batch(provider: str) -> bool:
    p = (provider or "").lower()
    return "local" in p or "ollama" in p


async def run_one_cell(
    provider: str,
    agent: CryptoAgent,
    cell: Dict[str, Any],
    sem: asyncio.Semaphore,
    *,
    batch_one: Any,
    generate_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    cfg = {
        "algorithm": cell["algorithm"],
        "mode": cell.get("mode"),
        "language": cell["language"],
    }

    async def _call() -> Dict[str, Any]:
        try:
            r = await batch_one(provider, cfg, agent=agent, generate_kwargs=generate_kwargs)
            elapsed = time.perf_counter() - t0
            out = _outcome_from_web_batch(r, agent, elapsed=elapsed)
            return {
                "ok": out["ok"],
                "error": out["error"],
                "gsr": out["gsr"],
                "vpr": out["vpr"],
                "ftpr": out["ftpr"],
                "seconds": out["seconds"],
                "filepath": out["filepath"],
                "vector_detail": out.get("vector_detail"),
                "generation_ok": out.get("generation_ok"),
                "history_skip": out.get("history_skip", False),
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "gsr": False,
                "vpr": False,
                "ftpr": False,
                "seconds": time.perf_counter() - t0,
                "filepath": None,
                "vector_detail": None,
                "generation_ok": False,
                "history_skip": False,
            }

    if _provider_is_local_batch(provider):
        async with sem:
            return await _call()
    return await _call()


def _markdown_table(
    rows: List[Dict[str, Any]],
    baseline_key: str,
    distill_key: str,
) -> str:
    lines = [
        "| 算法 | 模式 | 语言 | 无蒸馏 FTPR | 有蒸馏 FTPR |",
        "|------|------|------|:-------------:|:-------------:|",
    ]
    for r in rows:
        alg = r.get("algorithm", "")
        mode = r.get("mode") or "—"
        if str(alg).upper() == "RSA":
            mode = "—"
        lang = r.get("language", "")
        vb = r.get(baseline_key)
        vd = r.get(distill_key)
        b = "✓" if vb is True else ("—" if vb is None else "✗")
        d = "✓" if vd is True else ("—" if vd is None else "✗")
        lines.append(f"| {alg} | {mode} | {lang} | {b} | {d} |")
    return "\n".join(lines)


def _status_label(r: Dict[str, Any]) -> str:
    if r.get("history_skip"):
        return "HIST"
    return "Y" if r.get("ftpr") else "N"


async def async_main(*, default_algorithm: Optional[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qwen 本地：蒸馏关 vs 开，同网格 FTPR 对比。默认不调 LLM。"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--algorithm",
        choices=["DES", "AES", "RSA", "SM4"],
        default=None,
        help="仅跑指定算法子网格（可与 run_qwen_distill_<alg>.py 等价）",
    )
    parser.add_argument("--provider", default="qwen_coder_local")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--invoke", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="最多跑前 N 格（每 phase 相同子集）")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="保留兼容；当前 invoke 走 Web 批量单条，内部固定 3 轮，本参数不参与调用",
    )
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--skip-distill", action="store_true")
    parser.add_argument("-o", "--output-md", type=str, default="", help="Markdown 报告路径")
    parser.add_argument("--json-output", type=str, default="", help="原始 JSON 结果路径")
    parser.add_argument(
        "--checkpoint",
        type=str,
        default="",
        help="invoke 时：每完成一格写入检查点 JSON；重启后加 --resume 续跑（路径相对 experiments/results/）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="若检查点存在且与当前网格指纹一致，从上次进度继续",
    )
    parser.add_argument(
        "--merge-from",
        type=str,
        default="",
        help="从先前 JSON 结果合并 baseline/distill（按 cell 键对齐网格；路径相对 experiments/results/）",
    )
    parser.add_argument(
        "--refresh-cells",
        action="append",
        default=[],
        help="增量模式：baseline 与 distill 两阶段均刷新指定格，格式 ALG:MODE:LANG（可重复）",
    )
    parser.add_argument(
        "--refresh-baseline-cells",
        action="append",
        default=[],
        help="增量模式：仅无蒸馏阶段刷新指定格（未设则沿用 --refresh-cells / --update-sm4-ofb-python）",
    )
    parser.add_argument(
        "--refresh-distill-cells",
        action="append",
        default=[],
        help="增量模式：仅有蒸馏阶段刷新指定格（未设则沿用 --refresh-cells / --update-sm4-ofb-python）",
    )
    parser.add_argument(
        "--update-sm4-ofb-python",
        action="store_true",
        help="增量预设：合并先前 JSON，仅重跑 SM4|OFB|python 无蒸馏；有蒸馏列仅历史复测",
    )
    args = parser.parse_args()

    refresh_keys_baseline, refresh_keys_distill, partial_update = _resolve_refresh_keys(
        refresh_cells=args.refresh_cells,
        refresh_baseline_cells=args.refresh_baseline_cells,
        refresh_distill_cells=args.refresh_distill_cells,
        update_sm4_ofb_python=bool(args.update_sm4_ofb_python),
    )
    merge_raw = (args.merge_from or "").strip()
    if not merge_raw and partial_update:
        for candidate in (
            "experiments/qwen_distill_compare.json",
            "experiments/experiments/qwen_distill_compare.json",
        ):
            p = resolve_under_results(Path(candidate))
            if p.is_file():
                merge_raw = candidate
                break
    merge_path = resolve_under_results(Path(merge_raw)) if merge_raw else None
    if partial_update and not merge_path:
        raise SystemExit(
            "增量更新须指定 --merge-from，或确保 "
            "experiments/results/experiments/qwen_distill_compare.json 存在。"
        )
    if merge_path and not merge_path.is_file():
        raise SystemExit(f"--merge-from 文件不存在：`{merge_path}`")

    invoke = bool(args.invoke or args.live)
    dry = bool(args.dry_run or not invoke)

    cfg_path = ROOT / args.config if not Path(args.config).is_absolute() else Path(args.config)
    cfg = ConfigLoader(str(cfg_path))
    alg_filter = (args.algorithm or default_algorithm or "").strip().upper() or None
    if alg_filter:
        grid = build_grid_for_algorithm(cfg, alg_filter)
    else:
        grid = build_full_grid(cfg)
    if args.limit and args.limit > 0:
        grid = grid[: args.limit]

    n_teachers, pool_path = _teacher_pool_stats(cfg)
    sem_n = 1
    try:
        sem_n = max(1, min(8, int(cfg.get("local_batch_concurrency", 1) or 1)))
    except Exception:
        sem_n = 1
    sem = asyncio.Semaphore(sem_n)

    grid_desc = f"算法={alg_filter}" if alg_filter else "全算法"
    if partial_update:
        bk_labels = "|".join(f"{a}:{m}:{l}" for a, m, l in sorted(refresh_keys_baseline or []))
        dk_labels = "|".join(f"{a}:{m}:{l}" for a, m, l in sorted(refresh_keys_distill or []))
        print(
            f"[grid] 任务格数: {len(grid)}（{grid_desc}；增量更新）"
        )
        print(f"  无蒸馏刷新: {bk_labels or '—'}")
        print(f"  有蒸馏刷新: {dk_labels or '—'}（history_only，不调 LLM）")
    else:
        print(f"[grid] 任务格数: {len(grid)}（{grid_desc}；有蒸馏沿用历史，无蒸馏强制重跑）")
    print(f"[distill] 教师池条目数: {n_teachers}" + (f"（{pool_path}）" if pool_path else ""))
    if dry:
        print("[dry-run] 未调用 LLM。真实实验请加: --invoke")
        return 0

    if n_teachers == 0 and not args.skip_distill:
        print(
            "[warn] 教师 JSONL 为空或不存在：「有蒸馏」阶段可能与无蒸馏相同。"
            "请先填充 data/distillation_teacher.jsonl（例如用云端跑通后自动收集）。"
        )

    results_baseline: List[Optional[Dict[str, Any]]] = []
    results_distill: List[Optional[Dict[str, Any]]] = []

    batch_one = load_batch_generate_single()

    if merge_path:
        mb, md = _load_merge_results(grid, merge_path)
        results_baseline = _pad_results(mb, len(grid))
        results_distill = _pad_results(md, len(grid))
        print(
            f"[merge] 自 `{merge_path}` 载入 baseline **{_count_filled(results_baseline)}/{len(grid)}**，"
            f"distill **{_count_filled(results_distill)}/{len(grid)}**"
        )

    ck_raw = (args.checkpoint or "").strip()
    ck_path = resolve_under_results(Path(ck_raw)) if ck_raw else None

    grid_snapshot = [
        {"algorithm": c.get("algorithm"), "mode": c.get("mode"), "language": c.get("language")}
        for c in grid
    ]
    fp_payload = {
        "schema": "run_qwen_distillation_ablation",
        "config": str(cfg_path),
        "provider": args.provider,
        "algorithm": alg_filter,
        "grid": grid_snapshot,
        "limit": args.limit,
        "enable_validation": False,
        "web_batch_single": True,
        "max_retries": args.max_retries,
        "skip_baseline": args.skip_baseline,
        "skip_distill": args.skip_distill,
        "merge_from": str(merge_path) if merge_path else None,
        "refresh_baseline_cells": sorted(list(refresh_keys_baseline)) if refresh_keys_baseline else None,
        "refresh_distill_cells": sorted(list(refresh_keys_distill)) if refresh_keys_distill else None,
    }
    fp = fingerprint_from_payload(fp_payload)

    def _save_ck() -> None:
        if not ck_path:
            return
        atomic_write_json(
            ck_path,
            {
                "version": 1,
                "schema": fp_payload["schema"],
                "fingerprint": fp,
                "fp_payload": fp_payload,
                "results_baseline": [results_baseline[i] for i in range(len(grid))],
                "results_distill": [results_distill[i] for i in range(len(grid))],
            },
        )

    if ck_path and not merge_path:
        prev = load_json_optional(ck_path)
        if prev:
            if prev.get("fingerprint") != fp:
                raise SystemExit(
                    checkpoint_mismatch_message(
                        ck_path,
                        "网格/provider/与检查点指纹不一致（含 Web 批量对齐项）",
                    )
                )
            prev_b = list(prev.get("results_baseline") or [])
            prev_d = list(prev.get("results_distill") or [])
            if args.resume:
                results_baseline = _pad_results(prev_b, len(grid))
                results_distill = _pad_results(prev_d, len(grid))
                print(
                    f"[checkpoint] --resume：基线 **{_count_filled(results_baseline)}/{len(grid)}**，"
                    f"蒸馏 **{_count_filled(results_distill)}/{len(grid)}**",
                    file=sys.stderr,
                )
            elif prev_b or prev_d:
                print(
                    "[checkpoint] 检查点已有进度但未加 `--resume`，将清空进度从头运行（并覆盖检查点）。",
                    file=sys.stderr,
                )
        elif args.resume:
            print(f"[checkpoint] --resume 但文件不存在：`{ck_path}`", file=sys.stderr)

    results_baseline = _pad_results(results_baseline, len(grid))
    results_distill = _pad_results(results_distill, len(grid))

    # Phase 1: baseline（蒸馏关）
    if not args.skip_baseline:
        agent_b = CryptoAgent(
            config_path=str(cfg_path),
            provider=args.provider,
            enable_validation=False,
        )
        _apply_distillation_enabled(agent_b, False)
        if partial_update:
            print(
                "[phase] 无蒸馏 · 增量：仅刷新格强制重跑（distillation.enabled=false；"
                "max_retries=1 + Self-Refine）…"
            )
        else:
            print(
                "[phase] 无蒸馏（distillation.enabled=false，强制重跑、不沿用历史；"
                "max_retries=1，Self-Refine 仍按 config 默认 3 轮）…"
            )
        _baseline_gen_kw = {"_skip_history_retest": True, "max_retries": 1}
        for i, cell in enumerate(grid):
            if not _should_refresh_cell(cell, refresh_keys_baseline):
                continue
            if not partial_update and i < _count_filled(results_baseline):
                if results_baseline[i] is not None:
                    continue
            label = _cell_label(cell)
            r = await run_one_cell(
                args.provider, agent_b, cell, sem, batch_one=batch_one,
                generate_kwargs=_baseline_gen_kw,
            )
            r["cell"] = cell
            r["label"] = label
            results_baseline[i] = r
            print(f"  [{i+1}/{len(grid)}] {label}  FTPR={_status_label(r)}")
            _save_ck()
    else:
        print("[phase] skip baseline (--skip-baseline)")

    hist_b = sum(1 for r in results_baseline if r and r.get("history_skip"))
    ran_b = sum(
        1
        for i, cell in enumerate(grid)
        if _should_refresh_cell(cell, refresh_keys_baseline) and results_baseline[i] is not None
    )
    if results_baseline and not args.skip_baseline:
        if partial_update:
            print(f"[phase] 无蒸馏：本次重跑 {ran_b} 格；历史复测跳过 {hist_b}/{len(grid)}（刷新格应为 0）")
        else:
            print(f"[phase] 无蒸馏：历史复测跳过 {hist_b}/{len(grid)} 格（应为 0）")

    # Phase 2: distill（按配置文件恢复 enabled，通常为 true）
    if not args.skip_distill:
        agent_d = CryptoAgent(
            config_path=str(cfg_path),
            provider=args.provider,
            enable_validation=False,
        )
        yaml_dist = (cfg.get("distillation") or {}).get("enabled", True)
        _apply_distillation_enabled(agent_d, bool(yaml_dist))
        _distill_history_only = partial_update and refresh_keys_distill is not None
        if partial_update:
            print(
                f"[phase] 有蒸馏 · 增量：仅刷新格历史复测（enabled={yaml_dist}，"
                f"教师池 {n_teachers} 条；**history_only，不调 LLM**）…"
            )
        else:
            print(
                f"[phase] 有蒸馏（distillation.enabled={yaml_dist}，教师池 {n_teachers} 条，"
                "历史复测通过则跳过 LLM）…"
            )
        _distill_gen_kw: Optional[Dict[str, Any]] = (
            {"_history_only": True} if _distill_history_only else None
        )
        for i, cell in enumerate(grid):
            if not _should_refresh_cell(cell, refresh_keys_distill):
                continue
            if not partial_update and i < _count_filled(results_distill):
                if results_distill[i] is not None:
                    continue
            label = _cell_label(cell)
            r = await run_one_cell(
                args.provider, agent_d, cell, sem, batch_one=batch_one,
                generate_kwargs=_distill_gen_kw,
            )
            r["cell"] = cell
            r["label"] = label
            results_distill[i] = r
            print(f"  [{i+1}/{len(grid)}] {label}  FTPR={_status_label(r)}")
            _save_ck()
    else:
        print("[phase] skip distill (--skip-distill)")

    hist_d = sum(1 for r in results_distill if r and r.get("history_skip"))
    ran_d = sum(
        1
        for i, cell in enumerate(grid)
        if _should_refresh_cell(cell, refresh_keys_distill) and results_distill[i] is not None
    )
    if results_distill and not args.skip_distill:
        if partial_update:
            print(f"[phase] 有蒸馏：本次重跑 {ran_d} 格；全网格历史复测跳过 {hist_d}/{len(grid)}")
        else:
            print(f"[phase] 有蒸馏：历史复测跳过 {hist_d}/{len(grid)} 格")

    # Merge rows for table
    merged: List[Dict[str, Any]] = []
    for idx, cell in enumerate(grid):
        row = dict(cell)
        bk = "ftpr_baseline"
        dk = "ftpr_distill"
        row[bk] = _result_ftpr(results_baseline[idx] if idx < len(results_baseline) else None)
        row[dk] = _result_ftpr(results_distill[idx] if idx < len(results_distill) else None)
        if args.skip_baseline:
            row[bk] = None
        elif row[bk] is None:
            row[bk] = False
        if args.skip_distill:
            row[dk] = None
        elif row[dk] is None:
            row[dk] = False
        merged.append(row)

    results_baseline_out = [results_baseline[i] for i in range(len(grid))]
    results_distill_out = [results_distill[i] for i in range(len(grid))]

    n = len(merged)
    pass_b = (
        sum(1 for r in merged if r.get("ftpr_baseline") is True)
        if not args.skip_baseline
        else None
    )
    pass_d = (
        sum(1 for r in merged if r.get("ftpr_distill") is True)
        if not args.skip_distill
        else None
    )
    pct_b = (100.0 * pass_b / n) if (pass_b is not None and n) else None
    pct_d = (100.0 * pass_d / n) if (pass_d is not None and n) else None

    line_pass_b = (
        f"- 无蒸馏通过: **{pass_b} / {n}**（{pct_b:.1f}%）"
        if pass_b is not None
        else "- 无蒸馏通过: —（本运行 `--skip-baseline`）"
    )
    line_pass_d = (
        f"- 有蒸馏通过: **{pass_d} / {n}**（{pct_d:.1f}%）"
        if pass_d is not None
        else "- 有蒸馏通过: —（本运行 `--skip-distill`）"
    )

    hist_b_all = sum(1 for r in results_baseline if r and r.get("history_skip"))
    hist_d_all = sum(1 for r in results_distill if r and r.get("history_skip"))

    merge_note = ""
    if partial_update and merge_path:
        bk_s = ", ".join(f"{a}:{m}:{l}" for a, m, l in sorted(refresh_keys_baseline or []))
        dk_s = ", ".join(f"{a}:{m}:{l}" for a, m, l in sorted(refresh_keys_distill or []))
        merge_note = (
            f"- 增量合并自: `{merge_path.name}`\n"
            f"- 本次无蒸馏刷新: **{bk_s or '—'}**；有蒸馏刷新: **{dk_s or '—'}**（history_only）\n"
        )

    summary_lines = [
        "## Qwen 蒸馏前后对比（功能测试 FTPR）",
        "",
        f"- Provider: `{args.provider}`",
        f"- 算法范围: **{alg_filter or 'DES+AES+RSA+SM4（全网格）'}**",
        f"- 网格格数: **{n}**",
        merge_note.rstrip(),
        f"- 历史复测跳过（无蒸馏 / 有蒸馏）: **{hist_b_all}** / **{hist_d_all}**（无蒸馏应恒为 0；有蒸馏 `HIST`=未调 LLM）",
        line_pass_b,
        line_pass_d,
        f"- 教师池 JSONL 条目: **{n_teachers}**",
        "",
        _markdown_table(merged, "ftpr_baseline", "ftpr_distill"),
        "",
        f"*UTC {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}*",
    ]
    report = "\n".join(summary_lines)
    print()
    print(report)

    if args.output_md:
        outp = resolve_under_results(Path(args.output_md))
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(report, encoding="utf-8")
        print(f"[write] {outp}")

    if args.json_output:
        jpath = resolve_under_results(Path(args.json_output))
        jpath.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": args.provider,
            "algorithm": alg_filter,
            "grid_size": n,
            "history_skip_baseline": hist_b_all,
            "history_skip_distill": hist_d_all,
            "teacher_pool_lines": n_teachers,
            "teacher_pool_path": pool_path,
            "pass_baseline": pass_b,
            "pass_distill": pass_d,
            "skip_baseline": args.skip_baseline,
            "skip_distill": args.skip_distill,
            "merge_from": str(merge_path) if merge_path else None,
            "refresh_baseline_cells": sorted(list(refresh_keys_baseline)) if refresh_keys_baseline else None,
            "refresh_distill_cells": sorted(list(refresh_keys_distill)) if refresh_keys_distill else None,
            "partial_update": partial_update,
            "results_baseline": results_baseline_out,
            "results_distill": results_distill_out,
            "merged": merged,
        }
        jpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[write] {jpath}")

    return 0


def main_for_algorithm(algorithm: str) -> int:
    return asyncio.run(async_main(default_algorithm=algorithm.upper()))


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
