#!/usr/bin/env python3
"""
**提示策略**消融（``tab:ablation_prompt``）：与 Web 相同调用链 build_prompt -> PromptLoader.get_prompt。

**不是**论文「主要消融 / 表 7」（``tab:ablation_main``）；主消融请用 ``run_paper_ablation.py --suite main``。

**默认论文四档**（与目录语义对齐；批量 invoke 默认与主消融相同：**deepseek / doubao / claude / openai / codex** 中 config 已启用的云端）：
  ``common_only`` → 单一通用（仅 ``base_prompt.yaml`` 隔离段）；
  ``common_llm`` → 通用 + ``llms/<provider>/``（coder_bootstrap + io_constraints，不含 ``prompts/algorithms`` 算法 YAML / 语言子目录 / ``llms/.../algorithms``）；
  ``common_llm_lang`` → 在上档基础上 + ``prompts/algorithms`` 中算法层 + ``algorithms/<lang>/`` 语言层；
  ``full`` → 完整栈（含 failure_driven、``llms/.../algorithms/*.yaml``、蒸馏等），与 Web ``prompt_ablation=None`` 一致。

``full`` 档若已在 **Web** 上跑满，`--invoke` **默认**从项目根 ``code_history.db`` **回填「full」档**（与主消融脚本一致）；无记录再走 API。不再需要 ``--skip-full-invoke`` 也能省钱；跳过 full 仍可加 ``--skip-full-invoke``；强制 full 实时生成请加 ``--fresh-baseline``。

扩展档 ``base_prompt_only``、``llm_main_only``、``no_prompt`` **不**在默认列表中；需要时用 ``--ablation X`` 多次追加。

---

模式与 utils.prompt_loader.PromptLoader._ablation_allows 一致:
  full | common_only | common_llm | common_llm_lang | base_prompt_only | common_algorithm | common_algorithm_lang | common_algorithm_llm_main | llm_main_only | no_prompt

论文用语与仓库目录对应（``PromptLoader._ablation_allows``）：
  - ``common_only`` → 仅 ``prompts/common/base_prompt.yaml`` 隔离 ``base_prompt``
  - ``common_llm`` → 通用 + ``llms/<provider>/llm.yaml``（bootstrap）与 ``io_constraints.yaml``，**无** ``prompts/algorithms`` 下 DES/AES 模板与语言子目录
  - ``common_llm_lang`` → ``common_llm`` + 算法根/common/spec/mode + ``algorithms/<c|cpp|python>/``
  - ``full`` → 与 Web 一致的全栈（含 ``llms/<p>/algorithms/*.yaml``、failure_driven、蒸馏等）
  - 历史档 ``common_algorithm`` / ``common_algorithm_lang`` / ``common_algorithm_llm_main`` 仍可用 ``--ablation`` 指定

默认（不传 --algorithm）：**batch 时**在 **五家云端**（deepseek / doubao / claude / openai / codex，config 中已启用且非本地）× DES × des_modes × supported_languages。
不加 `--provider` 即只跑上述默认子集；加 `--provider` 则只跑该家。
**仅统计 prompt 长度，不调用 LLM**；真实生成需加 --invoke 或 --live。

与 Web **单页生成** API（``GenerateRequest``）对齐的默认：``validate=True``、``max_retries=3``、``operation=加密解密``。
Web **批量**接口 ``_batch_generate_single`` 使用 ``validate=False``、``max_retries=3``；若要对齐批量请加 ``--no-validate --max-retries 3``。

单任务须显式 ``--algorithm``；``--provider`` 仅允许 openai / claude / deepseek / doubao / codex，省略则默认 deepseek。

示例:
  python experiments/run_prompt_ablation.py
  python experiments/run_prompt_ablation.py -o prompt_lens.md --json-output raw.json
    （相对路径写入 experiments/results/。）
  python experiments/run_prompt_ablation.py --invoke
  python experiments/run_prompt_ablation.py --live --provider deepseek --algorithm AES --mode CBC --language python
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.prompt_builder import build_prompt  # noqa: E402
from agent.crypto_agent import CryptoAgent  # noqa: E402
from experiments.ablation_defaults import (  # noqa: E402
    ABLATION_EXPERIMENT_ALLOWED_PROVIDERS,
    ablation_cloud_providers_subset,
)
from utils.config_loader import ConfigLoader  # noqa: E402
from utils.prompt_loader import PromptLoader  # noqa: E402
from utils.test_data_loader import TestDataLoader  # noqa: E402
from experiments.experiment_outputs import resolve_under_results  # noqa: E402
from experiments.ablation_history_baseline import fetch_latest_cell_metrics  # noqa: E402
from experiments.experiment_checkpoint import (  # noqa: E402
    atomic_write_json,
    checkpoint_mismatch_message,
    fingerprint_from_payload,
    load_json_optional,
)


def _set_experiment_log_level(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    for name in (
        "CryptoAgent",
        "agent",
        "agent.code_saver",
        "agent.code_generator",
        "utils",
    ):
        logging.getLogger(name).setLevel(level)
    logging.getLogger().setLevel(level)


class _DryRunAgent:
    """仅用于长度统计：与 Web 相同的 prompt_loader / config / provider，不初始化 LLM。"""

    def __init__(self, provider: str, config_path: str = "config.yaml"):
        self.provider = provider
        self.config = ConfigLoader(config_path)
        self.prompt_loader = PromptLoader()
        self.openssl_dev_available = False
        self.test_data_loader = TestDataLoader()


# 论文 tab:ablation_prompt 默认四行（通用 → +LLM → +算法/语言模板 → 本文 full）
PAPER_PROMPT_ABLATIONS: Tuple[str, ...] = (
    "common_only",
    "common_llm",
    "common_llm_lang",
    "full",
)

# 可选扩展（须 --ablation 追加；不参与默认四档表）
OPTIONAL_EXTRA_ABLATIONS: Tuple[str, ...] = (
    "base_prompt_only",
    "common_algorithm_llm_main",
    "llm_main_only",
    "no_prompt",
)

ALL_ABLATION_CHOICES: Tuple[str, ...] = PAPER_PROMPT_ABLATIONS + (
    "common_algorithm",
    "common_algorithm_lang",
) + OPTIONAL_EXTRA_ABLATIONS

# 行名（默认四行 + 历史/扩展档）
ABLATION_LABELS = {
    "full": "四层可组合提示（本文方法，full 栈）",
    "common_only": "单一通用模板",
    "common_llm": "通用+LLM（llm.yaml + io_constraints）",
    "common_llm_lang": "通用+LLM+语言",
    "base_prompt_only": "仅 base_prompt.yaml（通用基座）",
    "common_algorithm": "通用+算法（历史档）",
    "common_algorithm_lang": "通用+算法+语言（历史档）",
    "common_algorithm_llm_main": "LLM-main（bootstrap+I/O，扩展档）",
    "llm_main_only": "仅 LLM 主模板层",
    "no_prompt": "无任何提示（no_prompt；仅单行任务描述，无领域模板栈）",
}

# 终端进度：论文章节四档对应的层级口径（与上表 internal 名对齐）
ABLATION_TIER_HINT = {
    "common_only": "通用",
    "common_llm": "通用+LLM",
    "common_llm_lang": "通用+LLM+语言",
    "full": "通用+LLM+语言+算法",
    "base_prompt_only": "仅 base_prompt（base_prompt_only）",
    "common_algorithm": "通用+算法（历史档）",
    "common_algorithm_lang": "通用+算法+语言（历史档）",
    "common_algorithm_llm_main": "通用+算法+语言+LLM-main（扩展）",
    "llm_main_only": "仅 LLM 主模板层",
    "no_prompt": "无提示（no_prompt；仅单行任务）",
}


def _ablation_tier_hint(pa: str) -> str:
    if pa in ABLATION_TIER_HINT:
        return ABLATION_TIER_HINT[pa]
    return ABLATION_LABELS.get(pa, pa)

DES_ALGO = "DES"


def _ablation_markdown_order(keys_present: Set[str]) -> List[str]:
    """Markdown 表行顺序：先论文章节四档，再其余档（按字母序）。"""
    ordered: List[str] = []
    for pa in ALL_ABLATION_CHOICES:
        if pa in keys_present and pa not in ordered:
            ordered.append(pa)
    for pa in sorted(keys_present - set(ordered)):
        ordered.append(pa)
    return ordered


def _cloud_provider_names(cfg: ConfigLoader) -> List[str]:
    """启用且视为「云端」的 LLM（排除蒸馏声明的本地 provider 与 localhost 网关）。"""
    raw = cfg._config.get("llm_providers", {})
    local_names = set(cfg.get("distillation", {}).get("local_providers") or [])
    out: List[str] = []
    for name, c in raw.items():
        if not c.get("enabled", False):
            continue
        if name in local_names:
            continue
        base = (c.get("base_url") or "").lower()
        if "127.0.0.1" in base or "localhost" in base:
            continue
        if name.endswith("_local"):
            continue
        out.append(name)
    return sorted(out)


def _resolve_test_data(
    agent: Union[CryptoAgent, "_DryRunAgent"],
    algorithm: str,
    mode: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not agent.test_data_loader:
        return None
    if algorithm.upper() == "RSA":
        return agent.test_data_loader.get_test_data(algorithm, None)
    return agent.test_data_loader.get_test_data(algorithm, mode)


def _measure(
    agent: Union[CryptoAgent, "_DryRunAgent"],
    algorithm: str,
    mode: Optional[str],
    operation: str,
    language: str,
    test_data: Optional[Dict[str, Any]],
    ablations: List[str],
) -> List[Dict[str, Any]]:
    rows = []
    for pa in ablations:
        text = build_prompt(
            agent,
            algorithm,
            mode=mode,
            operation=operation,
            language=language,
            test_data=test_data,
            prompt_ablation=pa,
        )
        rows.append(
            {
                "prompt_ablation": pa,
                "chars": len(text),
                "lines": text.count("\n") + (1 if text else 0),
            }
        )
    return rows


async def _invoke(
    agent: CryptoAgent,
    algorithm: str,
    mode: Optional[str],
    operation: str,
    language: str,
    max_retries: int,
    ablations: List[str],
    trace_generation: bool = False,
    *,
    validate: bool = True,
    requirements: Optional[str] = None,
    ablation_strict_c: bool = False,
    full_history_db: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    out = []
    n_pa = len(ablations)
    hist_full = None
    if (
        full_history_db
        and full_history_db.is_file()
        and "full" in ablations
    ):
        hist_full = fetch_latest_cell_metrics(
            full_history_db,
            algorithm=algorithm,
            provider=agent.provider,
            mode=mode or "",
            language=language,
        )

    for i, pa in enumerate(ablations, start=1):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tier = _ablation_tier_hint(pa)
        print(
            f"{ts} [aicrypto-helper]   prompt_ablation [{i}/{n_pa}] `{pa}` · {tier}",
            file=sys.stderr,
            flush=True,
        )
        if pa == "full" and hist_full is not None:
            print(
                f"{ts} [aicrypto-helper]       → `full` 已从历史回填 `{full_history_db.name}`（跳过 LLM）",
                file=sys.stderr,
                flush=True,
            )
            out.append(
                {
                    "prompt_ablation": pa,
                    "gsr": bool(hist_full["gsr"]),
                    "validation_ok": bool(hist_full["vpr"]),
                    "test_ok": bool(hist_full["ftpr"]),
                    "_from_code_history": True,
                }
            )
            continue
        extra: Dict[str, Any] = {"prompt_ablation": pa}
        # 论文消融「非 full 档禁用 canonical 整文件替换」与 Web 默认全量提示不完全一致；
        # 默认关闭；仅当 --ablation-strict-c 时启用旧行为。
        if ablation_strict_c and pa != "full":
            extra["_disable_canonical_c_replace"] = True
        if trace_generation:
            extra["_trace_generation"] = True
        if requirements and requirements.strip():
            extra["额外要求"] = requirements.strip()
        res = await agent.generate_and_save(
            algorithm=algorithm,
            mode=mode,
            operation=operation,
            language=language,
            validate=validate,
            max_retries=max_retries,
            **extra,
        )
        path, val, test, _ssl = res
        try:
            code = path.read_text(encoding="utf-8")
        except Exception:
            code = ""
        gsr = len(code.strip()) > 30
        out.append(
            {
                "prompt_ablation": pa,
                "gsr": gsr,
                "validation_ok": val[0] if val else None,
                "test_ok": test[0] if test else None,
            }
        )
    return out


def _fmt_metric(x: float) -> str:
    x = float(x)
    if abs(x - round(x, 1)) < 1e-9:
        return f"{x:.1f}"
    return f"{x:.2f}"


def _pct_true(xs: List[bool]) -> float:
    if not xs:
        return 0.0
    return round(100.0 * sum(1 for x in xs if x) / len(xs), 2)


def render_prompt_ablation_markdown(payload: Dict[str, Any]) -> str:
    """论文风格 Markdown：dry-run 为提示长度表；invoke 为 GSR/VPR/FTPR 表（对齐 tab:ablation_prompt 列名）。"""
    kind = payload.get("kind") or ""

    def sep4() -> List[str]:
        return ["| :--- | :---: | :---: | :---: |"]

    def sep3() -> List[str]:
        return ["| :--- | :---: | :---: |"]

    if kind == "dry_run_batch_des_cloud":
        # 按消融档聚合平均字符/行数
        sums_c: Dict[str, float] = {}
        sums_l: Dict[str, float] = {}
        cnt: Dict[str, int] = {}
        for run in payload.get("runs") or []:
            for row in run.get("ablations") or []:
                pa = row.get("prompt_ablation") or ""
                sums_c[pa] = sums_c.get(pa, 0.0) + float(row.get("chars") or 0)
                sums_l[pa] = sums_l.get(pa, 0.0) + float(row.get("lines") or 0)
                cnt[pa] = cnt.get(pa, 0) + 1
        lines = [
            "**【未调用 LLM】** 下表为本地拼接的提示词 **字符/行数** 统计；非模型生成结果。",
            "",
            "**表：各提示消融配置的平均提示长度（批量 DES × 云端 LLM × 模式 × 语言）**",
            "",
            "| 提示消融配置 | 平均字符数 | 平均行数 |",
        ]
        lines.extend(sep3())
        for pa in _ablation_markdown_order(set(cnt.keys())):
            if pa not in cnt:
                continue
            n = cnt[pa]
            ac = sums_c[pa] / n
            al = sums_l[pa] / n
            label = ABLATION_LABELS.get(pa, pa)
            lines.append(
                f"| {label} | {round(ac, 1)} | {round(al, 1)} |"
            )
        lines.append("")
        lines.append(
            "*说明：基于 `build_prompt` 文本统计；与论文 `tab:ablation_prompt` 中的性能表不同项（该表需 `--invoke` 跑通 API）。*"
        )
        return "\n".join(lines)

    if kind == "invoke_batch_des_cloud":
        lines = [
            "**【已调用 LLM】** 下表为 `generate_and_save` 在各提示消融档上的汇总指标。",
            "",
            "**表：提示策略消融实验结果（DES 全模式，云端 LLM；对应 tab:ablation_prompt 列）**",
            "",
            "| 提示策略 | GSR (%) | VPR (%) | FTPR (%) |",
        ]
        lines.extend(sep4())
        # 按 pa 收集指标
        gsr_m: Dict[str, List[bool]] = {}
        vpr_m: Dict[str, List[bool]] = {}
        ft_m: Dict[str, List[bool]] = {}
        for run in payload.get("runs") or []:
            if run.get("skipped"):
                continue
            for r in run.get("results") or []:
                pa = r.get("prompt_ablation") or ""
                gsr_m.setdefault(pa, []).append(bool(r.get("gsr")))
                vpr_m.setdefault(pa, []).append(bool(r.get("validation_ok")))
                ft_m.setdefault(pa, []).append(bool(r.get("test_ok")))
        have = set(gsr_m) | set(vpr_m) | set(ft_m)
        for pa in _ablation_markdown_order(have):
            if pa not in have:
                continue
            label = ABLATION_LABELS.get(pa, pa)
            g = _pct_true(gsr_m.get(pa) or [])
            v = _pct_true(vpr_m.get(pa) or [])
            f = _pct_true(ft_m.get(pa) or [])
            lines.append(
                f"| {label} | {_fmt_metric(g)} | {_fmt_metric(v)} | {_fmt_metric(f)} |"
            )
        return "\n".join(lines)

    if kind == "dry_run":
        rows = payload.get("ablations") or []
        lines = [
            "**【未调用 LLM】** 本表为单任务下各消融档的提示长度（本地统计）。",
            "",
            "**表：单任务各提示消融配置的提示长度**",
            "",
            "| 提示消融配置 | 字符数 | 行数 |",
        ]
        lines.extend(sep3())
        for row in rows:
            pa = row.get("prompt_ablation") or ""
            label = ABLATION_LABELS.get(pa, pa)
            lines.append(
                f"| {label} | {row.get('chars', '')} | {row.get('lines', '')} |"
            )
        return "\n".join(lines)

    if kind == "invoke":
        rows = payload.get("results") or []
        lines = [
            "**【已调用 LLM】** 单任务下各消融档的验证/测试结果。",
            "",
            "**表：单任务提示策略消融（GSR / VPR / FTPR）**",
            "",
            "| 提示策略 | GSR (%) | VPR (%) | FTPR (%) |",
        ]
        lines.extend(sep4())
        for row in rows:
            pa = row.get("prompt_ablation") or ""
            label = ABLATION_LABELS.get(pa, pa)
            g = 100.0 if row.get("gsr") else 0.0
            v = 100.0 if row.get("validation_ok") else 0.0
            f = 100.0 if row.get("test_ok") else 0.0
            if row.get("validation_ok") is None:
                v = 0.0
            if row.get("test_ok") is None:
                f = 0.0
            lines.append(
                f"| {label} | {_fmt_metric(g)} | {_fmt_metric(v)} | {_fmt_metric(f)} |"
            )
        return "\n".join(lines)

    return f"*（未知 kind={kind}，无法渲染论文表；请查看 JSON。）*"


def _batch_des_cloud_params(
    cfg: ConfigLoader,
    modes: Optional[Sequence[str]],
    languages: Optional[Sequence[str]],
    provider_filter: Optional[str],
) -> Tuple[List[str], List[str], List[str]]:
    providers = _cloud_provider_names(cfg)
    if provider_filter:
        pf = provider_filter.strip().lower()
        if pf not in ABLATION_EXPERIMENT_ALLOWED_PROVIDERS:
            raise SystemExit(
                "run_prompt_ablation 批量消融仅支持 openai / claude / deepseek / doubao / codex。"
                f"请将 --provider 设为其中之一，或省略以默认五家 deepseek/doubao/claude/openai/codex（收到 {provider_filter!r}）。"
            )
        providers = [p for p in providers if p.lower() == pf]
        if not providers:
            raise SystemExit(
                f"config 中未启用或不可用 provider `{provider_filter}`（须为云端且非 local）。"
            )
    else:
        providers = ablation_cloud_providers_subset(providers)
    mode_list = list(modes) if modes else (cfg.get("des_modes") or ["ECB", "CBC", "CFB", "OFB"])
    lang_list = list(languages) if languages else (cfg.get("supported_languages") or ["python", "c", "cpp"])
    return providers, mode_list, lang_list


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prompt 消融：默认只统计提示长度（不调 LLM）；"
            "须 --invoke/--live 才会调用 generate_and_save。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "批量模式且不加 --provider：默认 **deepseek / doubao / claude / openai / codex**（config 已启用者）逐一调用，与论文主消融一致。\n"
            "默认输出 Markdown 表；--json-output 可选。\n"
            "未加 --invoke 时输出的数字来自 len(prompt)，不是模型 logits。"
        ),
    )
    parser.add_argument(
        "--algorithm",
        default=None,
        help="指定则单任务模式；省略则批量：DES × 云端 LLM × des_modes × languages",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="可选。批量/单任务均只可 openai/claude/deepseek/doubao/codex；批量省略则五家 deepseek/doubao/claude/openai/codex（已启用）都跑，单任务省略则 deepseek",
    )
    parser.add_argument("--mode", default=None)
    parser.add_argument("--operation", default="加密解密")
    parser.add_argument("--language", default="python")
    parser.add_argument(
        "--modes",
        nargs="*",
        default=None,
        help="批量时 DES 子模式列表；默认取 config.yaml 中 des_modes",
    )
    parser.add_argument(
        "--languages",
        nargs="*",
        default=None,
        help="批量时语言列表；默认取 config.yaml 中 supported_languages",
    )
    parser.add_argument(
        "--ablation",
        action="append",
        choices=list(ALL_ABLATION_CHOICES),
        metavar="MODE",
        help="可多次指定 prompt_ablation；省略则默认四档（common_only / common_llm / common_llm_lang / full）",
    )
    parser.add_argument(
        "--fresh-baseline",
        action="store_true",
        help="禁用 code_history：`full` 档也一律调 LLM；默认 invoke 且含 full 时优先回填省钱",
    )
    parser.add_argument(
        "--history-db",
        type=Path,
        default=None,
        help="`full` 回填用 SQLite（默认：<项目根>/code_history.db；相对路径相对项目根）",
    )
    parser.add_argument(
        "--skip-full-invoke",
        action="store_true",
        help="与 --invoke 联用：不调用 full（本文方法）档，省 API；该档可从 Web 历史或 extract_llm_performance_from_history.py 对齐",
    )
    parser.add_argument(
        "--invoke",
        "--live",
        action="store_true",
        help="真实调用 generate_and_save（消耗 API）；省略则仅统计 prompt 长度",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印 INFO 级日志（默认 invoke 时仅 WARNING，终端不刷屏；模型回复仍主要在 JSON/落盘代码中）",
    )
    parser.add_argument(
        "--trace-prompt",
        action="store_true",
        help="每次 generate 在 stderr 打印 Prompt 组装与 LLM 调用（[aicrypto-trace]）；全文需 AICRYPTO_TRACE_PROMPT_FULL=1",
    )
    parser.add_argument("--max-retries", type=int, default=3, help="仅 --invoke 时有效（默认 3，与 Web 单页一致）")
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="generate 前不做编译/语法验证（与 Web 关闭「验证」或批量接口一致；默认做验证=与 Web 单页默认一致）",
    )
    parser.add_argument(
        "--requirements",
        default="",
        help="传入 Web 同名字段「额外要求」，将作为 kwargs 传入 generate_and_save",
    )
    parser.add_argument(
        "--ablation-strict-c",
        action="store_true",
        help="非 full 提示消融档对 C 代码禁止 canonical 整文件替换（旧论文消融口径；默认关闭以贴近 Web 全量提示的清洗行为）",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="批量模式：最多跑前 N 个 (provider, mode, language) 格（与 run_paper_ablation 一致；试跑省钱）",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="config 路径（dry-run 与 invoke 均读取蒸馏等配置）",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="论文版式 Markdown 表格路径（UTF-8）",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="可选：另存完整原始 JSON（批量长度或 invoke 明细）",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="invoke 批量模式：每完成一格写入检查点 JSON；关机后可配合 --resume 续跑",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="invoke 批量模式：若存在 --checkpoint 且指纹匹配，从上次已完成格继续",
    )
    args = parser.parse_args()

    if args.invoke:
        _set_experiment_log_level(args.verbose)

    ablations = list(args.ablation) if args.ablation else list(PAPER_PROMPT_ABLATIONS)
    if args.skip_full_invoke and args.invoke:
        ablations = [x for x in ablations if x != "full"]
        print(
            "[aicrypto-helper] 已 --skip-full-invoke：本轮不跑 full；"
            "『full（本文方法栈）』行请用 Web 全量提示历史或 extract_llm_performance_from_history.py 对齐。",
            file=sys.stderr,
        )
    cfg = ConfigLoader(args.config)

    validate_flag = not args.no_validate

    hist_db_prompt: Optional[Path] = None
    baseline_hist_prompt = False
    if args.invoke and "full" in ablations and not args.fresh_baseline:
        baseline_hist_prompt = True
        hist_db_prompt = args.history_db or (ROOT / "code_history.db")
        if not hist_db_prompt.is_absolute():
            hist_db_prompt = ROOT / hist_db_prompt

    if not args.invoke:
        print(
            "[aicrypto-helper] **当前未调用 LLM**：未加 --invoke 或 --live。"
            "输出中的「字符数/行数」来自本地 build_prompt，不是模型回复。\n"
            "若要真实跑通 API：python experiments/run_prompt_ablation.py --invoke\n"
            "（不加 --provider 时默认五家云端 deepseek/doubao/claude/openai/codex 已启用者；加 --provider 可只跑一家）",
            file=sys.stderr,
        )
    else:
        print(
            "[aicrypto-helper] 本次将调用云端 LLM（generate_and_save）。请确认 API 可用。"
            f" validate={'False' if args.no_validate else 'True'}（与 Web 单页默认一致 unless --no-validate）。",
            file=sys.stderr,
        )
        if baseline_hist_prompt:
            print(
                f"[aicrypto-helper] **省钱**：`prompt_ablation=full` 将优先从历史 **`{hist_db_prompt}`** 回填（无记录再生成）。"
                " **`--fresh-baseline`** 可关闭回填。",
                file=sys.stderr,
            )
        elif "full" in ablations and args.fresh_baseline:
            print(
                "[aicrypto-helper] **`--fresh-baseline`**：`full` 不从历史回填。",
                file=sys.stderr,
            )

    # ---------- 批量：DES × 云端 LLM ----------
    if args.algorithm is None:
        providers, modes, languages = _batch_des_cloud_params(
            cfg, args.modes, args.languages, args.provider
        )
        if not providers:
            raise SystemExit(
                "未找到可用的云端 LLM：批量默认 deepseek/doubao/claude/openai/codex，且须在 config 中 enabled、"
                "非 localhost、非 local_providers。或传 --provider 指定单家（含 openai）。"
            )
        cells: List[Tuple[str, str, str]] = [
            (prov, mode, lang)
            for prov in providers
            for mode in modes
            for lang in languages
        ]
        if args.max_cases is not None:
            cells = cells[: args.max_cases]
        if args.invoke:
            print(
                f"[aicrypto-helper] 批量模式：将对 **{len(providers)}** 家云端 LLM 逐一实验："
                + "、".join(providers),
                file=sys.stderr,
            )
        runs: List[Dict[str, Any]] = []
        if not args.invoke:
            # 复用同一 agent，仅切换 provider，避免重复加载 yaml
            if cells:
                base_agent = _DryRunAgent(cells[0][0], config_path=args.config)
                for prov, mode, lang in cells:
                    base_agent.provider = prov
                    test_data = _resolve_test_data(base_agent, DES_ALGO, mode)
                    rows = _measure(
                        base_agent,
                        DES_ALGO,
                        mode,
                        args.operation,
                        lang,
                        test_data,
                        ablations,
                    )
                    runs.append(
                        {
                            "provider": prov,
                            "algorithm": DES_ALGO,
                            "mode": mode,
                            "language": lang,
                            "has_test_data": bool(test_data),
                            "ablations": rows,
                        }
                    )
            payload: Dict[str, Any] = {
                "kind": "dry_run_batch_des_cloud",
                "paper_note": "DES 全模式 × 启用且非本地的云端 LLM × supported_languages",
                "config": args.config,
                "providers": providers,
                "modes": modes,
                "languages": languages,
                "max_cases": args.max_cases,
                "runs": runs,
            }
        else:
            total_units = len(cells)
            done_units = 0
            agent_by_prov: Dict[str, Optional[CryptoAgent]] = {}
            init_errors: Dict[str, str] = {}

            ck_path = (
                resolve_under_results(Path(args.checkpoint))
                if args.checkpoint
                else None
            )
            fp_payload = {
                "schema": "run_prompt_ablation.invoke_batch_des_cloud",
                "config": args.config,
                "providers": providers,
                "modes": modes,
                "languages": languages,
                "max_cases": args.max_cases,
                "ablations": list(ablations),
                "skip_full_invoke": bool(args.skip_full_invoke),
                "baseline_from_history": baseline_hist_prompt,
                "fresh_baseline": bool(args.fresh_baseline),
                "history_db": str(hist_db_prompt) if hist_db_prompt else "",
                "validate": validate_flag,
                "max_retries": args.max_retries,
                "requirements": (args.requirements or "").strip(),
                "ablation_strict_c": bool(args.ablation_strict_c),
                "operation": args.operation,
            }
            fp = fingerprint_from_payload(fp_payload)

            async def _invoke_all() -> List[Dict[str, Any]]:
                nonlocal done_units
                acc: List[Dict[str, Any]] = []
                start_i = 0
                if ck_path:
                    prev = load_json_optional(ck_path)
                    if prev:
                        if prev.get("fingerprint") != fp:
                            raise SystemExit(
                                checkpoint_mismatch_message(
                                    ck_path,
                                    "网格/消融档/validate 等与检查点记录不一致",
                                )
                            )
                        acc = list(prev.get("runs") or [])
                        start_i = len(acc)
                        if args.resume and start_i > 0:
                            print(
                                f"[aicrypto-helper] --resume：已从检查点恢复 **{start_i}/{len(cells)}** 格，继续后续任务。",
                                file=sys.stderr,
                            )
                        elif not args.resume and start_i > 0:
                            print(
                                f"[aicrypto-helper] 检查点已有 **{start_i}** 条记录但未加 `--resume`，将从头重跑并覆盖检查点。",
                                file=sys.stderr,
                            )
                            acc = []
                            start_i = 0
                    elif args.resume:
                        print(
                            f"[aicrypto-helper] --resume 但检查点不存在：`{ck_path}`，从零开始。",
                            file=sys.stderr,
                        )

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
                            "runs": acc,
                            "next_index": len(acc),
                            "total_cells": len(cells),
                        },
                    )

                done_units = start_i
                for idx in range(start_i, len(cells)):
                    prov, mode, lang = cells[idx]
                    if prov not in agent_by_prov:
                        try:
                            agent_by_prov[prov] = CryptoAgent(
                                config_path=args.config,
                                provider=prov,
                                enable_validation=validate_flag,
                            )
                        except Exception as e:
                            err_s = str(e)
                            init_errors[prov] = err_s
                            print(
                                f"[aicrypto-helper] **跳过** provider `{prov}`（CryptoAgent 初始化失败）：\n"
                                f"    {err_s[:800]}{'…' if len(err_s) > 800 else ''}",
                                file=sys.stderr,
                            )
                            agent_by_prov[prov] = None
                    agent = agent_by_prov.get(prov)
                    if agent is None:
                        acc.append(
                            {
                                "provider": prov,
                                "algorithm": DES_ALGO,
                                "mode": mode,
                                "language": lang,
                                "skipped": True,
                                "error": init_errors.get(prov, "CryptoAgent 初始化失败"),
                            }
                        )
                        _save_ck()
                        continue
                    done_units += 1
                    print(
                        f"[aicrypto-helper] LLM 调用 [{done_units}/{total_units}] "
                        f"{prov} | {mode} | {lang}（本格含 {len(ablations)} 次 prompt_ablation）",
                        file=sys.stderr,
                    )
                    res = await _invoke(
                        agent,
                        DES_ALGO,
                        mode,
                        args.operation,
                        lang,
                        args.max_retries,
                        ablations,
                        trace_generation=args.trace_prompt,
                        validate=validate_flag,
                        requirements=args.requirements or None,
                        ablation_strict_c=args.ablation_strict_c,
                        full_history_db=hist_db_prompt if baseline_hist_prompt else None,
                    )
                    acc.append(
                        {
                            "provider": prov,
                            "algorithm": DES_ALGO,
                            "mode": mode,
                            "language": lang,
                            "results": res,
                        }
                    )
                    _save_ck()
                return acc

            payload = {
                "kind": "invoke_batch_des_cloud",
                "paper_note": "DES 全模式 × 云端 LLM（需各提供商 API 可用）",
                "config": args.config,
                "providers": providers,
                "modes": modes,
                "languages": languages,
                "max_cases": args.max_cases,
                "runs": asyncio.run(_invoke_all()),
            }
            n_skip = sum(1 for r in payload["runs"] if r.get("skipped"))
            n_ok = len(payload["runs"]) - n_skip
            has_results = any("results" in r for r in payload["runs"])
            print(
                f"[aicrypto-helper] 批量 invoke 结束：run 记录 **{len(payload['runs'])}** 条"
                f"（skipped **{n_skip}**，含有效 `results` 的格 **{n_ok if has_results else 0}**）。",
                file=sys.stderr,
            )
            if n_skip > 0 and not has_results:
                print(
                    "[aicrypto-helper] **说明**：当前全部为 `skipped`，表示**每家 provider 在创建 CryptoAgent 时就失败**，"
                    "**没有执行任何 `generate_and_save`，因此没有调用 LLM。**\n"
                    "[aicrypto-helper] 请检查：**环境变量**（与 `api_key_env` 同名）、项目根 **`.api_keys.json`**（Web「保存密钥」写入的文件，CLI 会自动读取）、"
                    "或 **`AICRYPTO_API_KEYS_FILE`**；并确认 **base_url**、**enabled**。WSL 下可 `export` 后重试。",
                    file=sys.stderr,
                )
            elif has_results:
                print(
                    "[aicrypto-helper] 模型回复不会打在终端；请看 JSON 里 **`results`** 的 `validation_ok`/`test_ok`，"
                    "代码在 **output_dir**（默认 `generated_code/`）。",
                    file=sys.stderr,
                )

        md = render_prompt_ablation_markdown(payload)
        js = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output:
            outp = resolve_under_results(Path(args.output))
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(md, encoding="utf-8")
            print(f"已写入 Markdown: {outp}")
        else:
            print(md)
        if args.json_output:
            jp = resolve_under_results(Path(args.json_output))
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(js, encoding="utf-8")
            print(f"已写入 JSON: {jp}")
        return

    # ---------- 单任务 ----------
    algorithm = args.algorithm
    if algorithm.upper() == DES_ALGO and not args.mode:
        parser.error("单任务模式下 DES 必须指定 --mode")

    prov = args.provider or "deepseek"
    if prov.strip().lower() not in ABLATION_EXPERIMENT_ALLOWED_PROVIDERS:
        parser.error(
            "run_prompt_ablation 单任务仅支持 --provider 为 openai/claude/deepseek/doubao（或未指定则默认 deepseek）。"
        )
    if args.invoke:
        print(
            f"[aicrypto-helper] 单任务模式：仅调用 LLM `{prov}`（--provider 须为 openai/claude/deepseek/doubao 之一）。",
            file=sys.stderr,
        )
    agent: Union[CryptoAgent, _DryRunAgent]
    if args.invoke:
        agent = CryptoAgent(
            config_path=args.config,
            provider=prov,
            enable_validation=validate_flag,
        )
    else:
        agent = _DryRunAgent(prov, config_path=args.config)
    test_data = _resolve_test_data(agent, algorithm, args.mode)

    if args.invoke:
        results = asyncio.run(
            _invoke(
                agent,
                algorithm,
                args.mode,
                args.operation,
                args.language,
                args.max_retries,
                ablations,
                trace_generation=args.trace_prompt,
                validate=validate_flag,
                requirements=args.requirements or None,
                ablation_strict_c=args.ablation_strict_c,
                full_history_db=hist_db_prompt if baseline_hist_prompt else None,
            )
        )
        payload = {
            "kind": "invoke",
            "provider": prov,
            "algorithm": algorithm,
            "mode": args.mode,
            "operation": args.operation,
            "language": args.language,
            "results": results,
        }
    else:
        rows = _measure(
            agent,
            algorithm,
            args.mode,
            args.operation,
            args.language,
            test_data,
            ablations,
        )
        payload = {
            "kind": "dry_run",
            "provider": prov,
            "algorithm": algorithm,
            "mode": args.mode,
            "language": args.language,
            "operation": args.operation,
            "has_test_data": bool(test_data),
            "ablations": rows,
        }

    md = render_prompt_ablation_markdown(payload)
    js = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        outp = resolve_under_results(Path(args.output))
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(md, encoding="utf-8")
        print(f"已写入 Markdown: {outp}")
    else:
        print(md)
    if args.json_output:
        jp = resolve_under_results(Path(args.json_output))
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(js, encoding="utf-8")
        print(f"已写入 JSON: {jp}")


if __name__ == "__main__":
    main()
