#!/usr/bin/env python3
"""
针对 ``llm_performance.json`` 中「修复后仍未跑通」的失败样本（与 ``error_repair_aggregate``
口径一致：同 ``model_key + 算法 + 模式 + 语言`` 的时间序列里，该条失败之后不存在 ``test_success===true``），
按历史桶 **逐一追加一次** ``CryptoAgent.generate_and_save``，直至聚合「修复后通过率」达到 **100%**
或达到 ``--max-outer-rounds`` / 单次 ``--max-retries`` 仍失败。

**补跑用 LLM**：默认 **统一使用 ``deepseek``**（``--repair-provider``），**不**再按失败记录里的 ``qwen_coder_local:…`` 等去调本地模型；日志里仍会打印原始 ``model_key`` 便于对照性能 JSON。

**与「错误分类修复表」的关系**：本脚本**不**按 ``error_repair_aggregate`` 的四类错误做差异化修复，只对每个未解析 case 再调一次 ``generate_and_save``。论文/工具链里「修复后通过率」来自对 ``llm_performance.json`` 的**观测**（同 case 更晚是否成功），补跑仅有助于**产生**更晚的成功记录。

**前置**：在项目根目录执行（以便 ``CryptoAgent._record_performance`` 写入根目录 ``llm_performance.json``）。

用法:
  # 仅列出仍缺后续成功的 case（不调 LLM）
  python experiments/chase_repair_to_completion.py --dry-run

  # 真实补跑（默认每 case 内部最多 8 轮生成改进；外层最多 5 轮扫尾；LLM 固定 deepseek）
  python experiments/chase_repair_to_completion.py

  python experiments/chase_repair_to_completion.py --max-retries 12 --max-outer-rounds 8

  # 若需改用其它 provider（不推荐与「仅云端修复」混用）
  python experiments/chase_repair_to_completion.py --repair-provider openai
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.error_repair_aggregate import (  # noqa: E402
    CAT_ORDER,
    _build_case_lists_sorted,
    _case_key,
    _is_failure_record,
    _repair_resolved_after,
    flatten_performance_records,
    four_category_failure_experiment_stats,
)
from agent.crypto_agent import CryptoAgent  # noqa: E402

DEFAULT_PERF = ROOT / "llm_performance.json"


def _ensure_cwd_for_performance_json() -> None:
    """Agent 写入相对路径 llm_performance.json；尽量保证 cwd 为仓库根。"""
    cwd_pf = Path.cwd() / "llm_performance.json"
    root_pf = ROOT / "llm_performance.json"
    if not cwd_pf.is_file() and root_pf.is_file():
        os.chdir(ROOT)


def _load_flat(perf_path: Path) -> List[Dict[str, Any]]:
    raw = json.loads(perf_path.read_text(encoding="utf-8"))
    return flatten_performance_records(raw)


def _unresolved_failure_representatives(flat: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_case = _build_case_lists_sorted(flat)

    def _pass_true(x: Dict[str, Any]) -> bool:
        return x.get("test_success") is True

    failures = [r for r in flat if _is_failure_record(r)]
    unresolved = [r for r in failures if not _repair_resolved_after(r, by_case, _pass_true)]

    seen: Set[Tuple[str, str, str, str]] = set()
    reps: List[Dict[str, Any]] = []
    # 按 flat 顺序遍历，保证确定性；每 case 只补跑一次即可覆盖该 case 内所有「仍缺后续成功」的失败行
    for r in unresolved:
        k = _case_key(r)
        if k not in seen:
            seen.add(k)
            reps.append(r)
    return reps


def _aggregate_post_repair_pct(flat: List[Dict[str, Any]]) -> float:
    """与 error_repair 合计行一致：仅失败样本中「已跑通」占比。"""
    exp = four_category_failure_experiment_stats(flat)
    if exp["fail_total"] == 0:
        return 100.0
    return float(exp["overall_after"])


def _print_four_category_experiment_summary(flat: List[Dict[str, Any]], banner: str) -> None:
    """打印四类失败实验表（与 run_error_repair_table 同口径；成功记录不参与）。"""
    exp = four_category_failure_experiment_stats(flat)
    print(banner)
    print("  （总体：仅失败记录→归入四类；「已跑通%」= 本条或同 case 更晚 test_success）")
    hdr = f"  {'类型':<8} {'失败数':>7} {'修复前%':>9} {'已跑通%':>9}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for cat in CAT_ORDER:
        c = int(exp["counts"][cat])
        pb = exp["repair_pass_before"][cat]
        pa = exp["repair_pass_after"][cat]
        pb_s = "—" if pb is None else f"{pb:.2f}"
        pa_s = "—" if pa is None else f"{pa:.2f}"
        print(f"  {cat:<8} {c:>7} {pb_s:>9} {pa_s:>9}")
    ft = int(exp["fail_total"])
    ob = exp["overall_before"]
    oa = exp["overall_after"]
    ob_s = "—" if ob is None else f"{ob:.2f}"
    oa_s = "—" if oa is None else f"{oa:.2f}"
    print("  " + "-" * (len(hdr) - 2))
    print(f"  {'合计':<8} {ft:>7} {ob_s:>9} {oa_s:>9}")
    print()


async def _chase_one(
    agent: CryptoAgent,
    rec: Dict[str, Any],
    max_retries: int,
) -> Tuple[bool, str]:
    algo = (rec.get("algorithm") or "DES").strip()
    mode = rec.get("mode")
    if mode is not None:
        mode = str(mode).strip() or None
    lang = (rec.get("language") or "python").strip().lower()

    try:
        _path, val, test, _ssl = await agent.generate_and_save(
            algorithm=algo,
            mode=mode,
            operation="加密解密",
            language=lang,
            validate=True,
            max_retries=max_retries,
        )
    except Exception as e:
        return False, str(e)

    ftpr = bool(test and test[0])
    v_ok = bool(val and val[0]) if val is not None else False
    if ftpr:
        return True, "test_success"
    return False, f"仍未通过向量测试 (validation_ok={v_ok})"


def _get_or_create_agent(
    cache: Dict[str, Optional[CryptoAgent]],
    provider: str,
    config_path: Path,
) -> Optional[CryptoAgent]:
    if provider in cache:
        return cache[provider]
    try:
        ag = CryptoAgent(str(config_path), provider=provider)
        cache[provider] = ag
        return ag
    except Exception as e:
        logging.warning("CryptoAgent(%s) 初始化失败: %s", provider, e)
        cache[provider] = None
        return None


async def _run_round(
    reps: List[Dict[str, Any]],
    *,
    config_path: Path,
    max_retries: int,
    dry_run: bool,
    agent_cache: Dict[str, CryptoAgent | None],
    repair_provider: str,
) -> Tuple[int, int]:
    """返回 (success_count, fail_count)。"""
    ok_n = 0
    fail_n = 0
    prov = (repair_provider or "deepseek").strip().lower()
    for i, rec in enumerate(reps, start=1):
        mk = str(rec.get("_model_key") or "")
        algo = rec.get("algorithm")
        mode = rec.get("mode")
        lang = rec.get("language")
        label = f"[{i}/{len(reps)}] 源={mk} | {algo} | {mode} | {lang} | LLM={prov}"
        if dry_run:
            print(f"(dry-run) 将补跑 {label}")
            continue
        agent = _get_or_create_agent(agent_cache, prov, config_path)
        if agent is None:
            print(f"跳过 {label}：provider `{prov}` 不可用")
            fail_n += 1
            continue
        print(f"补跑 {label} …")
        ok, msg = await _chase_one(agent, rec, max_retries=max_retries)
        if ok:
            ok_n += 1
            print(f"  → 成功 ({msg})")
        else:
            fail_n += 1
            print(f"  → 失败: {msg}")
    return ok_n, fail_n


async def _async_main(args: argparse.Namespace) -> int:
    perf_path: Path = args.performance_json.resolve()
    if not perf_path.is_file():
        print(f"错误: 找不到 {perf_path}", file=sys.stderr)
        return 2

    _ensure_cwd_for_performance_json()

    flat = _load_flat(perf_path)
    before_pct = _aggregate_post_repair_pct(flat)
    _print_four_category_experiment_summary(flat, "【补跑前】四类失败 → 是否已跑通（向量测试）")
    print(
        f"合计：失败样本中已跑通占比 {before_pct}%（与 error_repair 表合计行「修复后通过率」同口径；"
        f"非「修复前通过率」）"
    )

    reps = _unresolved_failure_representatives(flat)
    print(f"仍缺后续成功的 case 数（按 model×任务去重）: {len(reps)}")
    if not reps:
        print("已为 100%，无需补跑。")
        return 0

    if args.dry_run:
        await _run_round(
            reps,
            config_path=args.config,
            max_retries=args.max_retries,
            dry_run=True,
            agent_cache={},
            repair_provider=args.repair_provider,
        )
        return 0

    agent_cache: Dict[str, Optional[CryptoAgent]] = {}
    outer = 0
    while outer < args.max_outer_rounds:
        outer += 1
        flat = _load_flat(perf_path)
        pct = _aggregate_post_repair_pct(flat)
        reps = _unresolved_failure_representatives(flat)
        if not reps:
            print(f"第 {outer} 轮扫描：修复后通过率已达 100%（当前日志口径）。")
            break
        print(f"\n=== 外层第 {outer}/{args.max_outer_rounds} 轮：剩余 {len(reps)} 个 case，当前通过率 {pct}% ===")
        await _run_round(
            reps,
            config_path=args.config,
            max_retries=args.max_retries,
            dry_run=False,
            agent_cache=agent_cache,
            repair_provider=args.repair_provider,
        )

    flat = _load_flat(perf_path)
    after_pct = _aggregate_post_repair_pct(flat)
    _print_four_category_experiment_summary(flat, "【补跑后】四类失败 → 是否已跑通（向量测试）")
    print(
        f"结束：失败样本合计已跑通占比 {before_pct}% → {after_pct}%（补跑前后对同一 JSON 口径；"
        f"上表为四类分项）。"
    )
    if after_pct < 100.0:
        reps_left = _unresolved_failure_representatives(flat)
        print(f"仍未跑通的 case 数: {len(reps_left)}（可提高 --max-retries / --max-outer-rounds 或检查 API/环境）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="补跑未解析失败 case，追求聚合修复后通过率 100%")
    ap.add_argument("--performance-json", type=Path, default=DEFAULT_PERF)
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--max-retries", type=int, default=8, help="generate_and_save 内部最大重试（改进轮数）")
    ap.add_argument(
        "--max-outer-rounds",
        type=int,
        default=5,
        help="外层扫描轮数（每轮对剩余 case 各补跑一次）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只列出待补跑 case，不请求 LLM")
    ap.add_argument(
        "--repair-provider",
        default="deepseek",
        help="补跑 generate_and_save 时统一使用的 LLM provider（默认 deepseek；不按失败记录的 model_key 选 qwen 等）",
    )
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    logging.basicConfig(level=logging.WARNING)

    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
