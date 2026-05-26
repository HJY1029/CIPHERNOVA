"""
在本地依次执行：与 Web 批量页相同的全部算法/模式/语言组合 × 指定 LLM。

默认**不会**自动遍历所有已启用 LLM（避免脚本悄悄换线路）；只跑 config.yaml 里的
``default_provider``（须为 enabled）。若要跑全部已启用线路，请加 ``--all-providers``，
或显式 ``--providers a b c``。

结果写入 JSON；无需启动 Web 服务。

用法（项目根目录）:
  python scripts/run_full_llm_matrix.py
  python scripts/run_full_llm_matrix.py --dry-run
  python scripts/run_full_llm_matrix.py --providers qwen_coder_local
  python scripts/run_full_llm_matrix.py --errors-log my_errors.txt
  python scripts/run_full_llm_matrix.py --no-errors-log
  python scripts/run_full_llm_matrix.py --providers deepseek openai
  python scripts/run_full_llm_matrix.py --all-providers

  # 只跑矩阵中前 12 条（刷新 llm_performance 时可分批执行）
  python scripts/run_full_llm_matrix.py --limit 12
  python scripts/run_full_llm_matrix.py --offset 12 --limit 12

  # 默认仅跑「未落库」或「历史复测未通过」的格；跑满网格请加 --all-slots
  python scripts/run_full_llm_matrix.py --providers doubao

失败用例默认写入 ``experiments/results/batch_generation_errors_<UTC>.txt``；仅 ``--providers qwen_coder_local`` 时默认为 ``qwen_batch_generation_errors_<UTC>.txt``。每条含 **error、vector_detail、已落盘的 generated_code**（若存在），便于改 prompt。

**跳过再生成（与 Web 批量共用逻辑）**：若 ``config.yaml`` 中 ``local_batch_skip_enabled`` 为 true，则每条会先查 ``code_history.db``：
同一 **provider** ×（算法/模式/语言）在 ``local_batch_skip_if_success_since`` 当日以来已有 **test_success** 记录时，先对历史源码做标准向量复测；通过则 **不调 LLM**，结果中 ``vector_detail`` 为「历史代码复测通过，未调用 LLM」。云端线路仅匹配同名 provider 的成功记录；本地线路沿用「任一本机线路」成功记录的原有语义。
单格需重新生成时，本地与云端一律 ``max_retries=3``（与 Web 单页一致）。

本脚本通过 ``importlib`` 加载 ``web.server._batch_generate_single``，与前端批量 API **同一实现**（非手写简化版）。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_batch_configs() -> list[dict]:
    algorithms = ["DES", "AES", "RSA", "SM4"]
    modes = {
        "DES": ["ECB", "CBC", "CFB", "OFB"],
        "AES": ["ECB", "CBC", "CFB", "OFB", "GCM", "CTR"],
        "SM4": ["ECB", "CBC", "CFB", "OFB"],
    }
    languages = ["python", "c", "cpp"]
    configs: list[dict] = []
    for alg in algorithms:
        if alg in modes:
            for mode in modes[alg]:
                for lang in languages:
                    configs.append({"algorithm": alg, "mode": mode, "language": lang})
        else:
            for lang in languages:
                configs.append({"algorithm": alg, "language": lang})
    return configs


def load_batch_single():
    spec = importlib.util.spec_from_file_location("ciphernova_web_server", ROOT / "web" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod._batch_generate_single


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="全算法/模式/语言矩阵批量生成（默认仅 default_provider，不自动切换多 LLM）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印组合数量与列表，不调用 API")
    parser.add_argument(
        "--all-providers",
        action="store_true",
        help="遍历 config 中所有已启用的 LLM（旧版默认行为；会切换多条线路）",
    )
    parser.add_argument(
        "--providers",
        nargs="*",
        default=None,
        help="显式指定一条或多条 provider（优先级最高，与 --all-providers 互斥时以本参数为准）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="结果 JSON 路径（默认 experiments/results/full_matrix_<utc>.json）",
    )
    parser.add_argument(
        "--errors-log",
        type=Path,
        default=None,
        help="将 success=False 的用例详情写入该文本（含 error、vector 等供改 prompt）；"
        "省略则默认 experiments/results/batch_generation_errors_<utc>.txt",
    )
    parser.add_argument(
        "--no-errors-log",
        action="store_true",
        help="不写入失败明细文本文件",
    )
    parser.add_argument(
        "--verbose-prompt-load",
        action="store_true",
        help="打印每个 prompts YAML 的加载详情（等价于环境变量 AICRYPTO_PROMPT_LOAD_VERBOSE=1；默认与其它 LLM 调用一致仅一条「YAML 已加载成功」）",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        metavar="N",
        help="跳过矩阵中前 N 条配置（与 --limit 联用可分批跑完全表）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="仅执行 N 条配置（默认跑满矩阵；用于抽样或分多次刷新性能日志）",
    )
    parser.add_argument(
        "--all-slots",
        action="store_true",
        help="跑满网格（默认启动前筛掉「历史复测已通过」的格，不再进入任务列表）",
    )
    args = parser.parse_args()

    from utils.batch_error_log import (
        append_batch_failure,
        default_matrix_errors_path,
        write_batch_error_log_header,
    )
    from utils.batch_pending import batch_skip_since, filter_configs_need_llm
    from utils.config_loader import ConfigLoader

    cfg = ConfigLoader(str(ROOT / "config.yaml"))
    enabled = cfg.get_enabled_llm_providers()
    if args.providers is not None and len(args.providers) > 0:
        providers = [p for p in args.providers if p in enabled]
        missing = [p for p in args.providers if p not in enabled]
        if missing:
            print(f"警告：以下 provider 未在 config 中启用或未配置，已跳过: {missing}", file=sys.stderr)
    elif args.all_providers:
        providers = list(enabled)
    else:
        default_p = (cfg.get("default_provider") or "").strip()
        if default_p and default_p in enabled:
            providers = [default_p]
            print(f"单线路模式：仅使用 config.default_provider = {default_p}", flush=True)
        elif enabled:
            providers = [enabled[0]]
            print(
                f"警告：default_provider「{default_p}」未启用或为空，改用首个已启用线路: {enabled[0]}",
                file=sys.stderr,
            )
        else:
            providers = []

    base_configs = build_batch_configs()
    only_pending = not args.all_slots
    since = batch_skip_since(cfg)
    pending_filter_stats: dict = {}
    configs = base_configs

    print(f"矩阵格数（未预筛）: {len(base_configs)}")
    print(f"将运行 providers ({len(providers)}): {', '.join(providers) or '(无)'}")
    if args.dry_run:
        hint = "；实际运行默认仅待生成格（历史复测预筛）" if only_pending else ""
        print(f"--dry-run：不执行预筛{hint}")
        for i, c in enumerate(base_configs[:5]):
            print(f"  示例 {i}: {c}")
        if len(base_configs) > 5:
            print(f"  ... 共 {len(base_configs)} 条")
        return 0

    if only_pending and len(providers) == 1:
        before = len(base_configs)
        configs, pending_filter_stats = await filter_configs_need_llm(
            providers[0],
            base_configs,
            cfg,
            db_path=str(ROOT / "code_history.db"),
            since=since,
        )
        print(
            f"仅待生成（since={since}，provider={providers[0]}）：网格 {before} → 待执行 {len(configs)} "
            f"(无成功落库 {pending_filter_stats.get('no_record', 0)}，"
            f"复测未过 {pending_filter_stats.get('retest_fail', 0)}，"
            f"复测通过跳过 {pending_filter_stats.get('retest_pass_skip', 0)})",
            flush=True,
        )

    if args.offset:
        if args.offset >= len(configs):
            print(f"错误: --offset {args.offset} 不小于矩阵长度 {len(configs)}", file=sys.stderr)
            return 2
        configs = configs[args.offset :]
    if args.limit is not None:
        if args.limit <= 0:
            print("错误: --limit 须为正整数", file=sys.stderr)
            return 2
        configs = configs[: args.limit]
    print(f"组合数（已应用 offset/limit）: {len(configs)}")
    print(f"总任务数: {len(configs) * len(providers)}")

    if not providers:
        print("没有可运行的 provider（请检查 config.yaml enabled）", file=sys.stderr)
        return 2

    if args.verbose_prompt_load:
        os.environ["AICRYPTO_PROMPT_LOAD_VERBOSE"] = "1"
    else:
        os.environ.pop("AICRYPTO_PROMPT_LOAD_VERBOSE", None)

    batch_one = load_batch_single()
    from experiments.experiment_outputs import experiments_results_dir

    out_path = args.out
    if out_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = experiments_results_dir() / f"full_matrix_{ts}.json"

    err_fp = None
    err_log_path: Optional[Path] = None
    if not args.no_errors_log:
        err_log_path = args.errors_log or default_matrix_errors_path(ROOT, providers)
        err_log_path.parent.mkdir(parents=True, exist_ok=True)
        err_fp = open(err_log_path, "w", encoding="utf-8")
        write_batch_error_log_header(
            err_fp,
            title="scripts/run_full_llm_matrix.py — 批量生成失败记录",
            extra_lines=[
                f"results_json: {out_path}",
                f"configs_per_provider: {len(configs)}",
                f"providers: {providers}",
            ],
        )

    report: dict = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "providers": providers,
        "configs_count": len(configs),
        "only_pending": only_pending,
        "since_used": since,
        "pending_filter_stats": pending_filter_stats,
        "results": [],
    }

    for prov in providers:
        prov_configs = configs
        prov_pending_stats = pending_filter_stats
        if only_pending and len(providers) > 1:
            before_p = len(base_configs)
            prov_configs, prov_pending_stats = await filter_configs_need_llm(
                prov,
                base_configs,
                cfg,
                db_path=str(ROOT / "code_history.db"),
                since=since,
            )
            if args.offset:
                if args.offset >= len(prov_configs):
                    prov_configs = []
                else:
                    prov_configs = prov_configs[args.offset :]
            if args.limit is not None:
                prov_configs = prov_configs[: args.limit]
            print(
                f"[{prov}] 仅待生成：网格 {before_p} → 待执行 {len(prov_configs)} "
                f"(无落库 {prov_pending_stats.get('no_record', 0)}，"
                f"复测未过 {prov_pending_stats.get('retest_fail', 0)}，"
                f"跳过 {prov_pending_stats.get('retest_pass_skip', 0)})",
                flush=True,
            )
        if not prov_configs:
            report["results"].append(
                {"provider": prov, "cases": [], "pending_filter_stats": prov_pending_stats}
            )
            continue

        prov_results: list[dict] = []
        for j, conf in enumerate(prov_configs):
            label = f"{conf.get('algorithm')}-{conf.get('mode', '')}-{conf['language']}".strip("-")
            print(f"[{prov}] ({j+1}/{len(prov_configs)}) {label} ...", flush=True)
            r = await batch_one(prov, conf)
            slim = {
                "config": conf,
                "success": r.get("success"),
                "error": r.get("error"),
                "vector_status": r.get("vector_status"),
                "total_ms": r.get("total_ms"),
                "case_id": r.get("case_id"),
                "generation_ok": r.get("generation_ok"),
            }
            prov_results.append(slim)
            if err_fp is not None:
                append_batch_failure(err_fp, provider=prov, config=conf, result=r)
            st = "OK" if r.get("success") else "FAIL"
            print(f"    -> {st} {r.get('total_ms')}ms", flush=True)
        report["results"].append(
            {
                "provider": prov,
                "cases": prov_results,
                "pending_filter_stats": prov_pending_stats if only_pending else None,
            }
        )

    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入: {out_path}")
    if err_fp is not None:
        err_fp.close()
        print(f"失败明细（供改进 prompt）: {err_log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
