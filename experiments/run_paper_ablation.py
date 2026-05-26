#!/usr/bin/env python3
"""
论文两类消融实验指标汇总（与 Web 相同 generate_and_save 调用链）。

**务必区分两类脚本：**

+------------------+------------------------------+----------------------------------------+
| 论文 LaTeX 标签   | 表意（中文行名）              | 本脚本                                 |
+==================+==============================+========================================+
| ``tab:ablation_main``  | **论文消融表（四行对照 + 完整方法）** | ``--suite main``                       |
+------------------+------------------------------+----------------------------------------+
| ``tab:ablation_prompt`` | **同上四行对照**（仅标签不同）   | ``--suite prompt``                     |
+------------------+------------------------------+----------------------------------------+
四行对照为：**完整所提方法** / **无测试反馈改进** / **无分层提示架构（仅 ``base_prompt.yaml``）** / **无任何提示（``no_prompt``）**。
**无测试反馈改进** 使用 ``_ablation_no_test_feedback``，并 **一并关闭** 原单独消融的 C/C++ 写盘前启发式修补
（原 ``_ablation_no_error_auto_repair`` 语义已并入其中）。
``main`` 与 ``prompt`` 的 **kwargs 与 Markdown 表结构相同**，区别仅为正文引用哪张表。

``experiments/run_prompt_ablation.py`` 才是 **prompt_ablation 档位阶梯**（common_only / common_llm / … / no_prompt），
**不等价**于本脚本的 ``--suite prompt``。

---

1) **论文消融四行对照**（``--suite main`` 或 ``--suite prompt`` → 表结构：系统配置 | VPR | FTPR | 性能下降）
   - **完整所提方法**：kwargs 空（与 Web 全量一致）。**``--invoke`` 时默认**从项目根 ``code_history.db`` **按格回填**该变体（节省 API）；缺史则仍调用 LLM。强制全量实时生成请加 ``--fresh-baseline``。
   - **无测试反馈改进**：``_ablation_no_test_feedback`` → code_saver **关闭** Self-Refine / improve 循环；
     **剥离** 向量失败时注入下一轮 generate 的 ``[VECTOR_TEST_RETRY]`` 摘要；并 **关闭** C/C++ 写盘前规则修补
     （EVP 头、截断剥离、``len``/``ciphertext`` 缓冲注入等，与 golden 整文件替换无关）
   - **无分层提示架构**：``prompt_ablation=common_only``，仅 ``prompts/common/base_prompt.yaml`` 中通用基座，不含算法/LLM 等分层模板。
   - **无任何提示**：``prompt_ablation=no_prompt`` → 不向模型加载分层领域模板；用户侧仅发送**一行最小任务描述**（算法/模式/语言/操作，见 ``PromptLoader._minimal_no_prompt_user_text``）。system 仍可为空，由 ``llm_user_content_for_api`` 保证 user 非空。测试反馈改进阶段可按 ``_ablation_no_test_feedback`` 等另行关闭。

指标（每格一条 DES×mode×language×provider 运行）：
- GSR：生成代码非空（保存文件长度足够）
- VPR：语法/编译验证通过（validation_result）
- FTPR：标准向量功能测试通过（test_result）

默认评测子集：DES × config.des_modes × supported_languages × **默认五家云端**（**deepseek / doubao / claude / openai / codex**，均以 `config.yaml` 中已启用且非本地为准；与论文主消融一致）。

**不传 `--provider` 时**：在上述五家之中**已启用的**云端上**逐一**调用。  
**传入 `--provider xxx`** 时只跑该一家（可为 ``openai`` / ``claude`` / ``deepseek`` / ``doubao`` / ``codex`` 之一）。

用法:
  # 仅预览任务规模（不调 LLM）；省略 --suite 时默认为 main（与 tab:ablation_main 一致）
  python experiments/run_paper_ablation.py
  python experiments/run_paper_ablation.py --suite prompt --dry-run

  # 真实调用云端 LLM（默认每格 max_retries=3；可先缩小规模）
  python experiments/run_paper_ablation.py --suite main --invoke --max-cases 1 --provider deepseek
  python experiments/run_paper_ablation.py --suite main --live -o ablation_main.md --json-output ablation_main.json

**断点续跑（``--invoke`` 默认开启）**：真实调用时默认将进度写入 ``experiments/results/paper_ablation_<suite>_ckpt.json``（每完成一格即原子保存）；中断或重启后**用相同命令行再加 ``--resume``** 可从 ``next_index`` 继续。若改网格/变体/``--suite`` 等导致指纹变化，须 ``--fresh`` 或删检查点后再跑。显式 ``--checkpoint 路径`` 可自定义文件；``--no-checkpoint`` 关闭自动写入。

相对路径的 ``-o`` / ``--json-output`` 写入 ``experiments/results/``。

默认 **不会** 请求任何 LLM；只有加了 --invoke/--live 才会产生真实推理与论文指标表。
**stdout** 为 Markdown 表格全文（便于终端直接查看）；若同时指定 **-o**，会先打印到 stdout 再写入文件。
可选 --json-output 另存原始汇总。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.config_loader import ConfigLoader  # noqa: E402
from agent.crypto_agent import CryptoAgent  # noqa: E402
from experiments.ablation_defaults import (  # noqa: E402
    ABLATION_EXPERIMENT_ALLOWED_PROVIDERS,
    ablation_cloud_providers_subset,
)
from experiments.experiment_checkpoint import (  # noqa: E402
    atomic_write_json,
    checkpoint_mismatch_message,
    fingerprint_from_payload,
    load_json_optional,
)
from experiments.ablation_history_baseline import paper_variant_row_from_history  # noqa: E402
from experiments.experiment_outputs import resolve_under_results  # noqa: E402

DES_ALGO = "DES"


def _cloud_provider_names(cfg: ConfigLoader) -> List[str]:
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


# (表格行名, generate_and_save 额外 kwargs；None 表示完整方法)
MAIN_SYSTEM_ABLATION: List[Tuple[str, Dict[str, Any]]] = [
    ("完整所提方法", {}),
    ("无测试反馈改进", {"_ablation_no_test_feedback": True}),
    ("无分层提示架构（仅 base_prompt.yaml）", {"prompt_ablation": "common_only"}),
    ("无任何提示", {"prompt_ablation": "no_prompt"}),
]

# 与 MAIN_SYSTEM_ABLATION 相同：tab:ablation_prompt 正文引用时用 --suite prompt
PROMPT_STRATEGY_ABLATION: List[Tuple[str, Dict[str, Any]]] = MAIN_SYSTEM_ABLATION


def _metrics_from_result(filepath: Path, val: Any, test: Any) -> Tuple[bool, bool, bool]:
    try:
        code = filepath.read_text(encoding="utf-8")
    except Exception:
        code = ""
    gsr = len(code.strip()) > 30
    vpr = bool(val and val[0])
    ftpr = bool(test and test[0])
    return gsr, vpr, ftpr


def _validation_hint(val: Any) -> Optional[str]:
    if val is None:
        return None
    if val[0]:
        return None
    msg = val[1] if len(val) > 1 else ""
    s = msg if isinstance(msg, str) else str(msg)
    return s[:480] if s else "(验证失败，无详情)"


def _test_hint(test: Any) -> Optional[str]:
    if test is None:
        return None
    if test[0]:
        return None
    msg = test[1] if len(test) > 1 else ""
    s = msg if isinstance(msg, str) else str(msg)
    return s[:480] if s else "(测试失败，无详情)"


async def _run_one_agent(
    agent: CryptoAgent,
    mode: str,
    language: str,
    max_retries: int,
    extra_kw: Dict[str, Any],
    trace_generation: bool = False,
    *,
    validate: bool = True,
) -> Dict[str, Any]:
    kw = dict(extra_kw)
    if kw.get("prompt_ablation") is None:
        kw.pop("prompt_ablation", None)
    if trace_generation:
        kw["_trace_generation"] = True

    try:
        path, val, test, _ssl = await agent.generate_and_save(
            algorithm=DES_ALGO,
            mode=mode,
            operation="加密解密",
            language=language,
            validate=validate,
            max_retries=max_retries,
            **kw,
        )
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "code_chars": 0,
            "gsr": False,
            "vpr": False,
            "ftpr": False,
            "validation_hint": None,
            "test_hint": None,
        }

    gsr, vpr, ftpr = _metrics_from_result(path, val, test)
    try:
        ncode = len(path.read_text(encoding="utf-8").strip())
    except Exception:
        ncode = 0
    out: Dict[str, Any] = {
        "ok": True,
        "filepath": str(path),
        "code_chars": ncode,
        "gsr": gsr,
        "vpr": vpr,
        "ftpr": ftpr,
    }
    if val is None:
        out["validation_hint"] = (
            "generate_and_save 返回 validation_result=None（请确认 enable_validation 与 CodeValidator）"
        )
    else:
        h = _validation_hint(val)
        if h:
            out["validation_hint"] = h
    th = _test_hint(test)
    if th:
        out["test_hint"] = th
    return out


def _get_or_create_agent(
    cache: Dict[str, Optional[CryptoAgent]],
    init_errors: Dict[str, str],
    config_path: str,
    provider: str,
    enable_validation: bool = True,
) -> Optional[CryptoAgent]:
    if provider in cache:
        return cache[provider]
    try:
        cache[provider] = CryptoAgent(
            config_path=config_path,
            provider=provider,
            enable_validation=enable_validation,
        )
        return cache[provider]
    except Exception as e:
        init_errors[provider] = str(e)
        cache[provider] = None
        return None


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


def _diagnostics_for_suite(
    definitions: List[Tuple[str, Dict[str, Any]]],
    out_rows: Dict[str, List[Dict[str, Any]]],
    init_errors: Dict[str, str],
) -> Dict[str, Any]:
    base_label = definitions[0][0]
    base_rows = out_rows.get(base_label) or []
    n = len(base_rows)
    ok_n = sum(1 for r in base_rows if r.get("ok"))
    gsr_n = sum(1 for r in base_rows if r.get("ok") and r.get("gsr"))
    vpr_n = sum(1 for r in base_rows if r.get("ok") and r.get("vpr"))
    ftpr_n = sum(1 for r in base_rows if r.get("ok") and r.get("ftpr"))

    hints: List[str] = []
    if n > 0 and n < 8:
        hints.append(
            f"**样本量过小**：基线变体仅 **{n}** 个 (provider×mode×language) 格子（例如用了 `--max-cases`），"
            "GSR/VPR/FTPR 易出现 **0% 或 100%**，**不能**当作完整论文网格结论；请去掉截断或增大 `--max-cases`。"
        )
    if init_errors:
        hints.append(
            "**CryptoAgent 初始化失败**（该 provider 全部跳过）："
            + "；".join(f"`{k}`: {v[:120]}" for k, v in init_errors.items())
        )

    samples: List[str] = []
    for r in base_rows:
        if not r.get("ok") and r.get("error"):
            samples.append(f"异常: {r['error'][:200]}")
        elif r.get("validation_hint"):
            samples.append(f"验证: {r['validation_hint'][:200]}")
        elif r.get("test_hint"):
            samples.append(f"测试: {r['test_hint'][:200]}")
        if len(samples) >= 6:
            break
    if samples:
        hints.append("**基线变体典型失败摘录**（`" + base_label + "`）：")
        hints.extend(f"- {s}" for s in samples)

    if n > 0 and ok_n > 0 and gsr_n == 0:
        hints.append(
            "**解读**：请求未抛错，但 **GSR=0**（保存代码过短）。多为 LLM 返回空/截断、或提取不到代码块；"
            "请 `--verbose` 看生成日志，并检查 API Key、网络、模型上下文与 `variant_run_summary[].code_chars`。"
        )
    if n > 0 and ok_n > 0 and gsr_n > 0 and vpr_n == 0:
        hints.append(
            "**解读**：已有足够长度代码但 **VPR=0**，说明**编译/语法验证未过**（WSL 下请装 `build-essential`、"
            "`libssl-dev`，Python 需可运行）。可先用 `--languages python --max-cases 1` 缩小问题。"
        )
    if n > 0 and ok_n == 0:
        hints.append(
            "**解读**：全部 `ok=false` 多为 LLM 调用异常或 Agent 初始化失败；请检查 API Key、网络与 config.yaml。"
        )

    err_blob = " ".join(str(r.get("error") or "") for r in base_rows)
    if (
        n > 0
        and ok_n == 0
        and "suppress_heuristic_warnings" in err_blob
        and "save_code" in err_blob
    ):
        hints.append(
            "**已定位（代码版本）**：部分分支曾误把 `suppress_heuristic_warnings` 传给 `save_code()`，导致 "
            "**每一格**均 TypeError、指标全 0。请同步最新 `agent/code_saver.py`（`save_code` 仅接收 "
            "`allow_canonical_whole_file` / `allow_error_auto_repair`；该标志只传给 `CodeTester.test`）。"
        )

    hist_hits = sum(
        1 for r in base_rows if r.get("_from_code_history")
    )
    if hist_hits > 0:
        hints.insert(
            0,
            f"**完整方法回填**：其中有 **{hist_hits}/{n}** 格来自 **`code_history.db`** 最新记录，未调用 LLM；"
            "缺史的格子仍实时生成。"
            "若要全部重新跑模型，请加 **`--fresh-baseline`**",
        )

    return {
        "baseline_label": base_label,
        "baseline_total": n,
        "baseline_ok_runs": ok_n,
        "baseline_gsr_hits": gsr_n,
        "baseline_vpr_hits": vpr_n,
        "baseline_ftpr_hits": ftpr_n,
        "baseline_history_hits": hist_hits,
        "agent_init_errors": init_errors or None,
        "hint_lines": hints,
    }


def _append_prompt_suite_interpretation(lines: List[str]) -> None:
    """tab:ablation_prompt：说明与 main 共用同一套四行对照配置。"""
    lines.append("")
    lines.append(
        "*说明：`--suite prompt` 与 `--suite main` 使用相同的五条系统配置与同一 Markdown 表结构；"
        "若正文只需一张消融表，任选其一即可。提示词阶梯消融请用 `experiments/run_prompt_ablation.py`。*"
    )


def _append_markdown_diagnostics(lines: List[str], result: Dict[str, Any]) -> None:
    diag = result.get("diagnostics") or {}
    hl = diag.get("hint_lines") or []
    foot: List[str] = []
    if diag.get("baseline_total"):
        suite = result.get("suite") or ""
        baseline_note = ""
        foot.append(
            f"- **基线变体** `{diag.get('baseline_label')}`{baseline_note}："
            f"完成 generate 请求 **{diag.get('baseline_ok_runs')}/{diag.get('baseline_total')}**，"
            f"GSR 命中 **{diag.get('baseline_gsr_hits')}**，"
            f"VPR 命中 **{diag.get('baseline_vpr_hits')}**，FTPR 命中 **{diag.get('baseline_ftpr_hits')}**"
        )
    if not foot and not hl:
        return
    lines.append("")
    lines.append("### 运行诊断")
    lines.extend(foot)
    if hl:
        lines.append("")
        lines.extend(hl)


def _pct(a: int, b: int) -> float:
    return round(100.0 * a / b, 2) if b else 0.0


def _fmt_metric(x: float) -> str:
    """与论文表一致：贴近整数用一位小数，否则两位。"""
    x = float(x)
    if abs(x - round(x, 1)) < 1e-9:
        return f"{x:.1f}"
    return f"{x:.2f}"


def _aggregate(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    n = len(rows)
    if not n:
        return {"GSR": 0.0, "VPR": 0.0, "FTPR": 0.0, "n": 0}
    return {
        "GSR": _pct(sum(1 for r in rows if r.get("gsr")), n),
        "VPR": _pct(sum(1 for r in rows if r.get("vpr")), n),
        "FTPR": _pct(sum(1 for r in rows if r.get("ftpr")), n),
        "n": float(n),
    }


def _drop_vs_full(full_rates: Dict[str, float], row_rates: Dict[str, float]) -> str:
    """相对完整方法的百分点变化（当前 − 完整），与论文表 tab:ablation_main 一致为负号形式。"""
    dv = row_rates["VPR"] - full_rates["VPR"]
    df = row_rates["FTPR"] - full_rates["FTPR"]
    return f"{_fmt_metric(dv)}% / {_fmt_metric(df)}%"


async def _run_suite(
    suite_name: str,
    definitions: List[Tuple[str, Dict[str, Any]]],
    providers: List[str],
    modes: List[str],
    languages: List[str],
    config_path: str,
    max_retries: int,
    max_cases: Optional[int],
    dry_run: bool,
    verbose_logs: bool,
    trace_generation: bool = False,
    *,
    enable_validation: bool = True,
    checkpoint_path: Optional[Path] = None,
    resume: bool = False,
    fresh: bool = False,
    baseline_from_history: bool = False,
    history_db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    tasks_desc: List[Dict[str, Any]] = []
    for prov in providers:
        for mode in modes:
            for lang in languages:
                tasks_desc.append(
                    {"provider": prov, "mode": mode, "language": lang}
                )

    if max_cases is not None:
        tasks_desc = tasks_desc[: max_cases]

    out_rows: Dict[str, List[Dict[str, Any]]] = {label: [] for label, _ in definitions}
    # 各行均以第一行「完整所提方法」为基准计算性能下降
    full_label = definitions[0][0]

    if dry_run:
        return {
            "suite": suite_name,
            "dry_run": True,
            "cloud_llm_providers": list(providers),
            "would_run_total": len(tasks_desc) * len(definitions),
            "tasks": tasks_desc,
            "variants": [l for l, _ in definitions],
        }

    _set_experiment_log_level(verbose_logs)

    agent_cache: Dict[str, Optional[CryptoAgent]] = {}
    init_errors: Dict[str, str] = {}
    flat_tasks: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    for label, extra_kw in definitions:
        for t in tasks_desc:
            flat_tasks.append((label, extra_kw, t))
    total_cells = len(flat_tasks)

    fp_payload = {
        "schema": "run_paper_ablation.suite_invoke",
        "suite_name": suite_name,
        "definitions": [(lab, json.dumps(kw, sort_keys=True, default=str)) for lab, kw in definitions],
        "tasks_desc": tasks_desc,
        "config_path": config_path,
        "max_retries": max_retries,
        "enable_validation": enable_validation,
        "trace_generation": trace_generation,
        "baseline_from_history": baseline_from_history,
        "history_db": str(history_db_path) if history_db_path else "",
    }
    fp = fingerprint_from_payload(fp_payload)

    start_i = 0
    ck_path = Path(checkpoint_path) if checkpoint_path else None
    if ck_path and fresh:
        print(
            f"[aicrypto-helper] --fresh：忽略检查点 `{ck_path}`，从零开始（运行中仍会写入该路径）。",
            file=sys.stderr,
        )
    if ck_path and not fresh:
        prev = load_json_optional(ck_path)
        if prev:
            if prev.get("fingerprint") != fp:
                raise SystemExit(
                    checkpoint_mismatch_message(
                        ck_path,
                        "suite/任务网格/验证开关等与检查点不一致",
                    )
                )
            loaded = prev.get("out_rows") or {}
            if isinstance(loaded, dict):
                out_rows = {k: list(v) for k, v in loaded.items()}
            start_i = int(prev.get("next_index", 0))
            if start_i > total_cells:
                raise SystemExit(
                    f"检查点 next_index={start_i} 超过当前任务数 {total_cells}，请删除或修正检查点文件。"
                )
            if resume and start_i > 0:
                print(
                    f"[aicrypto-helper] --resume：论文消融已从检查点恢复 **{start_i}/{total_cells}** 条任务，继续运行。",
                    file=sys.stderr,
                )
            elif not resume and start_i > 0:
                print(
                    f"[aicrypto-helper] 检查点已有 **{start_i}** 条记录但未加 `--resume`，将从头重跑并覆盖检查点。",
                    file=sys.stderr,
                )
                out_rows = {label: [] for label, _ in definitions}
                start_i = 0
        elif resume:
            print(
                f"[aicrypto-helper] --resume 但检查点不存在：`{ck_path}`，从零开始。",
                file=sys.stderr,
            )

    def _save_ck(next_index: int) -> None:
        if not ck_path:
            return
        atomic_write_json(
            ck_path,
            {
                "version": 1,
                "schema": fp_payload["schema"],
                "fingerprint": fp,
                "fp_payload": fp_payload,
                "suite": suite_name,
                "out_rows": out_rows,
                "next_index": next_index,
                "total_tasks": total_cells,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        )

    done = start_i
    for idx in range(start_i, len(flat_tasks)):
        label, extra_kw, t = flat_tasks[idx]
        done += 1
        prov = t["provider"]
        ag = _get_or_create_agent(
            agent_cache,
            init_errors,
            config_path,
            prov,
            enable_validation=enable_validation,
        )
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"{ts} [aicrypto-helper]   系统配置 · {label}  |  "
            f"{prov} | {t['mode']} | {t['language']}  [{done}/{total_cells}]",
            file=sys.stderr,
            flush=True,
        )
        r: Optional[Dict[str, Any]] = None
        if baseline_from_history and history_db_path and label == full_label and not extra_kw:
            r = paper_variant_row_from_history(history_db_path, case=t, algorithm=DES_ALGO)
            if r:
                print(
                    f"{ts} [aicrypto-helper]       → 已从历史库回填「完整所提方法」（未调 LLM）"
                    f" `{history_db_path.name}`",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    f"{ts} [aicrypto-helper]       → 历史中无此格记录，「完整所提方法」将调 LLM",
                    file=sys.stderr,
                    flush=True,
                )

        if ag is None:
            if r is None:
                r = {
                    "ok": False,
                    "error": init_errors.get(prov, "CryptoAgent 不可用"),
                    "code_chars": 0,
                    "gsr": False,
                    "vpr": False,
                    "ftpr": False,
                }
        else:
            if r is None:
                r = await _run_one_agent(
                    ag,
                    t["mode"],
                    t["language"],
                    max_retries,
                    extra_kw,
                    trace_generation=trace_generation,
                    validate=enable_validation,
                )
        r.update({"case": t, "variant": label})
        out_rows.setdefault(label, []).append(r)
        _save_ck(idx + 1)

    summary: Dict[str, Any] = {
        "suite": suite_name,
        "cloud_llm_providers": list(providers),
        "diagnostics": _diagnostics_for_suite(definitions, out_rows, init_errors),
        "variants": {},
    }
    full_rates: Optional[Dict[str, float]] = None

    for label, rows in out_rows.items():
        metrics_rows: List[Dict[str, bool]] = []
        for x in rows:
            if x.get("ok"):
                metrics_rows.append(
                    {"gsr": bool(x["gsr"]), "vpr": bool(x["vpr"]), "ftpr": bool(x["ftpr"])}
                )
            else:
                metrics_rows.append({"gsr": False, "vpr": False, "ftpr": False})
        rates = _aggregate(metrics_rows)
        summary["variants"][label] = {
            "rates_pct": rates,
            "failed_or_skipped": sum(1 for x in rows if not x.get("ok")),
            "detail_count": len(rows),
        }
        if label == full_label:
            full_rates = rates

    if full_rates and suite_name in ("main", "prompt"):
        summary["performance_drop_vs_full"] = {}
        for label, _ in definitions:
            if label == full_label:
                summary["performance_drop_vs_full"][label] = "– / –"
                continue
            r = summary["variants"][label]["rates_pct"]
            summary["performance_drop_vs_full"][label] = _drop_vs_full(full_rates, r)

    summary["variant_run_summary"] = {
        label: [
            {
                "provider": r["case"]["provider"],
                "mode": r["case"]["mode"],
                "language": r["case"]["language"],
                "ok": r.get("ok"),
                "code_chars": r.get("code_chars"),
                "gsr": r.get("gsr"),
                "vpr": r.get("vpr"),
                "ftpr": r.get("ftpr"),
                "error": r.get("error"),
                "validation_hint": r.get("validation_hint"),
                "test_hint": r.get("test_hint"),
            }
            for r in rows
        ]
        for label, rows in out_rows.items()
    }

    return summary


def _append_cloud_llm_line(lines: List[str], result: Dict[str, Any]) -> None:
    prov = result.get("cloud_llm_providers") or []
    if not prov:
        return
    lines.append(
        f"- **覆盖云端 LLM（共 {len(prov)} 家）**：" + "、".join(f"`{p}`" for p in prov)
    )
    lines.append("")


def _append_sample_scale_notes(lines: List[str], result: Dict[str, Any]) -> None:
    """避免将「试跑截断」（如 --max-cases 1）下的全 100% 误读为论文级结论。"""
    diag = result.get("diagnostics") or {}
    n = int(diag.get("baseline_total") or 0)
    if n <= 0:
        return
    lines.append("### 统计基数（必读）")
    lines.append(
        f"- 下表每一行（每个变体）均在同一套 **n = {n}** 个任务格上计算百分比；"
        f"每格为一次 **`generate_and_save`**（`provider` × `DES 模式` × `language`）。"
        f"**各行共用同一 n**，因此若 n 很小且各格都成功，容易出现**多行同为 100%**，不代表策略无差异。"
    )
    if n == 1:
        lines.append(
            "- **⚠️ 当前 n = 1**（运行诊断中「完成 generate 请求 1/1」即属此类）："
            "只要这一格通过，GSR/VPR/FTPR 都会是 100%，**不能**作为消融表的正式结果。"
            "**请去掉 `--max-cases` 或设为足够大的网格后重跑。**"
        )
    elif n < 16:
        lines.append(
            "- **提示**：n 仍偏少，百分比波动大；与「全模式 × 多语言 ×（多提供商）」的完整设定相比，结论需谨慎用于正文。"
        )
    lines.append("")


def render_ablation_markdown(result: Dict[str, Any]) -> str:
    """论文 tab:ablation_main / tab:ablation_prompt 版式 Markdown（首列左对齐，数值居中）。"""
    if result.get("dry_run"):
        lines = [
            "**【未调用 LLM】** 以下为消融实验**规模预览**（无任何 API 请求）。",
            "",
            "**表（消融规模预览）**",
            "",
            f"- **suite**: `{result.get('suite')}`",
            f"- **预运行任务格数**（provider×mode×lang）: **{len(result.get('tasks') or [])}**",
            f"- **变体数**: **{len(result.get('variants') or [])}**",
            f"- **总调用次数估计**: **{result.get('would_run_total', 0)}**",
            "",
            "- **将参与的云端 LLM（未传 --provider 时为全部）**："
            + "、".join(f"`{p}`" for p in (result.get("cloud_llm_providers") or [])),
            "",
            "变体列表：" + "、".join(result.get("variants") or []),
            "",
            "*跑完整实验请加 `--invoke`（与 `--dry-run` 互斥）。完整结果将输出与论文一致的表格。*",
        ]
        return "\n".join(lines)

    suite = result.get("suite") or ""
    variants = result.get("variants") or {}
    lines: List[str] = []

    if suite == "main":
        lines.append("**【已调用 LLM】** 下表为真实运行 `generate_and_save` 后的汇总指标。")
        _append_cloud_llm_line(lines, result)
        _append_sample_scale_notes(lines, result)
        lines.append("**表：主要消融实验结果**（对应 `tab:ablation_main`）")
        lines.append("")
        header = "| 系统配置 | VPR (%) | FTPR (%) | 性能下降 (VPR/FTPR) |"
        sep = "| :--- | :---: | :---: | :---: |"
        lines.extend([header, sep])
        drops = result.get("performance_drop_vs_full") or {}
        order = [label for label, _ in MAIN_SYSTEM_ABLATION]
        for label in order:
            v = variants.get(label) or {}
            r = (v.get("rates_pct") or {})
            drop = drops.get(label, "– / –")
            lines.append(
                "| "
                + " | ".join(
                    [
                        label,
                        _fmt_metric(r.get("VPR", 0)),
                        _fmt_metric(r.get("FTPR", 0)),
                        drop if isinstance(drop, str) else str(drop),
                    ]
                )
                + " |"
            )
        _append_markdown_diagnostics(lines, result)
        return "\n".join(lines)

    # prompt suite：与 main 相同的四行对照配置与表结构（对应 tab:ablation_prompt）
    lines.append("**【已调用 LLM】** 下表为真实运行 `generate_and_save` 后的汇总指标。")
    _append_cloud_llm_line(lines, result)
    _append_sample_scale_notes(lines, result)
    lines.append("**表：论文消融实验结果**（对应 `tab:ablation_prompt`；与 `tab:ablation_main` 四行对照一致）")
    lines.append("")
    header = "| 系统配置 | VPR (%) | FTPR (%) | 性能下降 (VPR/FTPR) |"
    sep = "| :--- | :---: | :---: | :---: |"
    lines.extend([header, sep])
    drops = result.get("performance_drop_vs_full") or {}
    order = [label for label, _ in MAIN_SYSTEM_ABLATION]
    for label in order:
        v = variants.get(label) or {}
        r = v.get("rates_pct") or {}
        drop = drops.get(label, "– / –")
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    _fmt_metric(r.get("VPR", 0)),
                    _fmt_metric(r.get("FTPR", 0)),
                    drop if isinstance(drop, str) else str(drop),
                ]
            )
            + " |"
        )
    _append_prompt_suite_interpretation(lines)
    _append_markdown_diagnostics(lines, result)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="论文主消融 / 提示策略消融（GSR/VPR/FTPR）。默认不调 LLM，须 --invoke 或 --live。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "重要：未加 --invoke/--live 时不会调用任何云端模型，只输出任务规模说明。\n"
            "省略 --suite 时默认为 main（论文主表 tab:ablation_main）；提示策略表请显式加 --suite prompt。\n"
            "不加 --provider 时，默认对 **deepseek / doubao / claude / openai / codex** 中 config 已启用的云端各跑一遍（论文主消融五家）。\n"
            "可选 `--provider codex` 等单独只跑一家。\n"
            "试跑缩小规模可：--invoke --max-cases 1 --languages python\n"
            "另：--json-output 可查看每条 validation_hint；加 --verbose 显示完整 INFO 日志。\n"
            "加 --trace-prompt 在 stderr 逐步打印每次 build_prompt 与 LLM.generate（或设环境变量 AICRYPTO_TRACE_GENERATION=1）；\n"
            "完整提示全文：AICRYPTO_TRACE_PROMPT_FULL=1（日志量极大）。\n"
            "默认 CryptoAgent(enable_validation=True) 且 generate_and_save(validate=True)，与 Web **单页生成**一致；\n"
            "若要对齐 Web **批量**接口，可加 `--no-validate --max-retries 3`。\n"
            "断点续跑：`--invoke` 默认将进度写入 `experiments/results/paper_ablation_<suite>_ckpt.json`；"
            "中断后**原命令加 `--resume`** 继续。改网格/变体后请 `--fresh` 或删该 JSON。"
        ),
    )
    ap.add_argument(
        "--suite",
        choices=("main", "prompt"),
        default="main",
        help="main / prompt：均为同一套四行对照论文消融（完整所提方法等）；区别仅为 LaTeX 表标签 tab:ablation_main vs tab:ablation_prompt（默认 main）",
    )
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument(
        "--provider",
        default=None,
        help="可选；只跑该云端供应商（openai / claude / deepseek / doubao / codex）。省略则默认 deepseek/doubao/claude/openai/codex 中已启用者各跑",
    )
    ap.add_argument(
        "--modes",
        nargs="*",
        default=None,
        help="DES 模式列表；默认 config.des_modes",
    )
    ap.add_argument(
        "--languages",
        nargs="*",
        default=None,
        help="语言列表；默认 config.supported_languages",
    )
    ap.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="传给 generate_and_save；过小易全表为 0（默认 3，与 Web 单页常见设置一致）",
    )
    ap.add_argument(
        "--no-validate",
        action="store_true",
        help="CryptoAgent(enable_validation=False) 且 generate_and_save(validate=False)；对齐 Web 批量生成而非单页",
    )
    ap.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="最多跑多少个 (provider,mode,language) 组合（截断）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出将要执行的规模，不调 API",
    )
    ap.add_argument(
        "--invoke",
        "--live",
        action="store_true",
        help="真实调用云端 LLM（消耗 API）；省略则不调模型，仅输出规模预览",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="打印 CryptoAgent 等 INFO 级日志（默认实验过程仅 WARNING，减少刷屏）",
    )
    ap.add_argument(
        "--trace-prompt",
        action="store_true",
        help="每次生成在 stderr 打印：Prompt 已加载（节选/全文）与 LLM 调用起止；等价于传 _trace_generation",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="另存论文版式 Markdown（UTF-8）；运行结束仍会在 **stdout** 打印同一份，便于终端看表",
    )
    ap.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="可选：另存原始汇总 JSON（便于程序处理）",
    )
    ap.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="检查点 JSON 路径（相对路径落在 experiments/results/）。省略且 --invoke 时默认 paper_ablation_<suite>_ckpt.json；与 --no-checkpoint 互斥",
    )
    ap.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="--invoke 时不写入/不恢复检查点（关闭默认自动检查点）",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="若存在检查点且指纹与当前参数一致，从 next_index 继续；未加本项且检查点已有进度则从头重跑并覆盖",
    )
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="有检查点文件也强制从零开始（仍可在运行中写入检查点；与 --resume 同用时以 --fresh 为准）",
    )
    ap.add_argument(
        "--fresh-baseline",
        action="store_true",
        help="禁用 code_history 回填：「完整所提方法」每格均调用 LLM；默认 --invoke 时优先从项目根 code_history.db 回填以省 API",
    )
    ap.add_argument(
        "--history-db",
        type=Path,
        default=None,
        help="回填用的 SQLite；默认 <项目根>/code_history.db；相对路径相对项目根",
    )
    args = ap.parse_args()

    # ConfigLoader 使用与 CryptoAgent 同名的默认 logger，须先压日志再加载配置，避免刷屏
    dry = args.dry_run or not args.invoke
    if not dry:
        _set_experiment_log_level(args.verbose)

    cfg = ConfigLoader(args.config)
    if args.provider:
        pf = args.provider.strip().lower()
        if pf not in ABLATION_EXPERIMENT_ALLOWED_PROVIDERS:
            sys.exit(
                "run_paper_ablation 消融实验 --provider 仅支持 openai / claude / deepseek / doubao / codex。"
                f"请将 --provider 设为其中之一，或省略以默认五家 deepseek/doubao/claude/openai/codex（收到 {args.provider!r}）。"
            )
    providers = _cloud_provider_names(cfg)
    if args.provider:
        providers = [p for p in providers if p.lower() == args.provider.strip().lower()]
        if not providers:
            sys.exit(f"云端提供商未找到或未启用: {args.provider}")
    else:
        providers = ablation_cloud_providers_subset(providers)
    if not providers:
        sys.exit(
            "未找到可用的云端 LLM：未传 --provider 时默认使用 config 中已启用的 "
            "deepseek / doubao / claude / openai / codex（至少一家）。"
        )
    modes = list(args.modes) if args.modes else (cfg.get("des_modes") or ["ECB", "CBC", "CFB", "OFB"])
    languages = list(args.languages) if args.languages else (cfg.get("supported_languages") or ["python"])

    defs = MAIN_SYSTEM_ABLATION
    suite_key = "main" if args.suite == "main" else "prompt"

    # 与 _run_suite 内顺序一致：provider → mode → language，便于解释 --max-cases 实际覆盖了谁
    tasks_preview: List[Dict[str, Any]] = []
    for prov in providers:
        for mode in modes:
            for lang in languages:
                tasks_preview.append({"provider": prov, "mode": mode, "language": lang})
    full_grid_n = len(tasks_preview)
    if args.max_cases is not None:
        tasks_preview = tasks_preview[: args.max_cases]

    if dry:
        if args.dry_run:
            print(
                "[aicrypto-helper] 已指定 --dry-run：仅统计任务规模，不调用 LLM。",
                file=sys.stderr,
            )
        else:
            print(
                "[aicrypto-helper] **当前未调用 LLM**：未加 --invoke 或 --live。"
                "若要进行真实消融（会请求云端 API），请追加例如：\n"
                "  python experiments/run_paper_ablation.py --invoke --max-cases 1\n"
                "（suite 默认为 main；提示策略表请加 --suite prompt。stdout 为 Markdown；若另存了 --json-output，那是机器汇总结构，不是模型逐字回复。）",
                file=sys.stderr,
            )
    else:
        print(
            "[aicrypto-helper] 本次将调用云端 LLM（已启用 --invoke/--live）。请确认 API 额度与 config.yaml。",
            file=sys.stderr,
        )
        prov_line = "、".join(providers) if providers else "（无）"
        print(
            f"[aicrypto-helper] 将对 **{len(providers)}** 家云端 LLM 逐一实验：{prov_line}",
            file=sys.stderr,
        )
        if not providers:
            print(
                "[aicrypto-helper] 警告：当前没有符合条件的云端供应商，请检查 config.yaml 的 enabled / local_providers。",
                file=sys.stderr,
            )
        if args.trace_prompt:
            print(
                "[aicrypto-helper] 已启用 --trace-prompt：stderr 将逐步输出 [aicrypto-trace]（Prompt 组装 + LLM 调用）。"
                "完整提示设 AICRYPTO_TRACE_PROMPT_FULL=1。",
                file=sys.stderr,
            )
        print(
            f"[aicrypto-helper] **任务格**：全网格共 **{full_grid_n}** 格（{len(providers)} 家 × {len(modes)} 模式 × {len(languages)} 语言）；"
            f"每个变体会对**每一格**各跑一次 `generate_and_save`（故总 LLM 次数 ≈ 格数 × 变体数）。",
            file=sys.stderr,
        )
        if not args.fresh_baseline:
            _hdb = args.history_db or (ROOT / "code_history.db")
            _hdb = _hdb if _hdb.is_absolute() else (ROOT / _hdb)
            print(
                f"[aicrypto-helper] **省钱默认**：「完整所提方法」将优先从 **`{_hdb}`** 按格回填（无记录再调 LLM）；"
                "其它三行变体仍正常调 API。强制全部实时生成请加 **`--fresh-baseline`**。",
                file=sys.stderr,
            )
        else:
            print(
                "[aicrypto-helper] 已 **`--fresh-baseline`**：完整方法不从历史回填。",
                file=sys.stderr,
            )
        if args.no_validate:
            print(
                "[aicrypto-helper] **验证**：已 `--no-validate` → `enable_validation=False`，`validate=False`（对齐 Web 批量）。",
                file=sys.stderr,
            )
        else:
            print(
                "[aicrypto-helper] **验证**：默认 `enable_validation=True`，`validate=True`（对齐 Web 单页）。",
                file=sys.stderr,
            )
        if args.max_cases is not None:
            skipped = full_grid_n - len(tasks_preview)
            prov_used = sorted({t["provider"] for t in tasks_preview})
            prov_missed = [p for p in providers if p not in prov_used]
            print(
                f"[aicrypto-helper] **⚠️ 已截断**：`--max-cases {args.max_cases}` → 本次**只跑 {len(tasks_preview)} 格**"
                f"（省略后 **{skipped}** 格）。实际覆盖云端：**{'、'.join(prov_used) or '（无）'}**。",
                file=sys.stderr,
            )
            if prov_missed:
                print(
                    f"[aicrypto-helper] **未在本轮触达的云端**（因截断排在后面）：**{'、'.join(prov_missed)}**。"
                    "若要 **4 家全跑**，请**去掉 `--max-cases`**，或设为 ≥ 全网格格数。",
                    file=sys.stderr,
                )
            if tasks_preview:
                t0 = tasks_preview[0]
                print(
                    f"[aicrypto-helper] 截断后首格示例：`{t0['provider']}` | `{t0['mode']}` | `{t0['language']}`（日志里 `[i/总]` 的 **总** = 格数×变体数）。",
                    file=sys.stderr,
                )

    validate_flag = not args.no_validate

    baseline_hist = bool(not dry and not args.fresh_baseline)
    history_db_resolved: Optional[Path] = None
    if baseline_hist:
        history_db_resolved = args.history_db or (ROOT / "code_history.db")
        if not history_db_resolved.is_absolute():
            history_db_resolved = ROOT / history_db_resolved

    ck_resolved: Optional[Path] = None
    if args.no_checkpoint:
        if args.checkpoint is not None:
            print(
                "[aicrypto-helper] 已同时指定 --checkpoint 与 --no-checkpoint：按 --no-checkpoint，不写检查点。",
                file=sys.stderr,
            )
    elif args.checkpoint is not None:
        ck_resolved = resolve_under_results(Path(args.checkpoint))
    elif not dry:
        ck_resolved = resolve_under_results(Path(f"paper_ablation_{suite_key}_ckpt.json"))
        print(
            f"[aicrypto-helper] 未指定 --checkpoint：真实调用默认检查点 `{ck_resolved}`；续跑请保持相同 CLI 并加 `--resume`。",
            file=sys.stderr,
        )

    result = asyncio.run(
        _run_suite(
            suite_key,
            defs,
            providers,
            modes,
            languages,
            args.config,
            args.max_retries,
            args.max_cases,
            dry,
            args.verbose,
            trace_generation=args.trace_prompt,
            enable_validation=validate_flag,
            checkpoint_path=ck_resolved,
            resume=args.resume,
            fresh=args.fresh,
            baseline_from_history=baseline_hist,
            history_db_path=history_db_resolved,
        )
    )

    if not dry and providers:
        diag = result.get("diagnostics") or {}
        init_errs = diag.get("agent_init_errors") or {}
        if init_errs and all(p in init_errs for p in providers):
            print(
                "[aicrypto-helper] **注意**：所列云端提供商均未成功初始化 CryptoAgent，**本次没有向任何 LLM 发请求**。"
                "若密钥只在 Web 里配置过，请确认仓库根目录存在 **`.api_keys.json`**（或导出同名环境变量）；详情见输出 Markdown 的「运行诊断」。",
                file=sys.stderr,
            )

    md = render_ablation_markdown(result)
    js = json.dumps(result, ensure_ascii=False, indent=2)
    # 始终将论文版式 Markdown 打到 stdout，便于终端直接看到表 11 样式（若仅用 -o 以前不会输出表格）
    print(md)

    if args.output:
        outp = resolve_under_results(Path(args.output))
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(md, encoding="utf-8")
        print(f"已写入 Markdown: {outp}", file=sys.stderr)
    if args.json_output:
        jp = resolve_under_results(Path(args.json_output))
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(js, encoding="utf-8")
        print(f"已写入 JSON: {jp}", file=sys.stderr)


if __name__ == "__main__":
    main()
