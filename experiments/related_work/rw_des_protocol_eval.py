#!/usr/bin/env python3
"""
相关工作对比：DES 子集（12 格）与论文 ``tab:rw_same_protocol_des`` 同一评测口径。

**生成**：SecCoder / SVEN / Self-Refine / AgentCoder 等须在**各自官方仓库**内用**其模型与推理脚本**完成；**勿**使用本项目的 ``PromptLoader``、``build_prompt``、``CryptoAgent``。
本脚本**不包含**任何外部基线的推理逻辑。

**相关工作对比口径**：生成由各基线**自己的数据与模型**在其官方环境中完成；本脚本仅在必要时 ``export`` 任务向量，并在 ``score`` 时用与本项目相同的 **GSR / VPR / FTPR**（FTPR 严格依据 ``test_data.yaml``）。

用法：
  1) export — 导出任务与向量约定（JSONL）；含 ``scoring_io_contract_*``，仅说明若要在本仓库通过 ``CodeTester`` 时**产物代码**需满足的 I/O，**不是**让基线套用的「本文提示词」。
  2) score — 将基线环境已生成并保存的源码按约定文件名放入目录，用 ``CodeValidator`` + ``CodeTester`` 打 GSR/VPR/FTPR（与 ``generate_and_save`` 同一评测链）。

约定文件名（与 ``agent/code_saver.save_code`` 默认命名一致，便于对照）：
  ``des_<mode>.py`` | ``des_<mode>.c`` | ``des_<mode>.cpp``
  其中 ``<mode>`` 为小写 ecb/cbc/cfb/ofb。同一目录下 12 个文件互不覆盖。

示例（**整行一条**最省事；``--inputs`` 须换成你本机**真实目录**，勿保留 ``path/to/...`` 占位）：

  # Linux / macOS / WSL / Git Bash（续行用反斜杠，**不要用** Windows 的 ^）
  python experiments/related_work/rw_des_protocol_eval.py score \\
    --inputs /path/to/seccoder_out/universal --arm seccoder_universal \\
    --no-canonical-whole-file \\
    -o experiments/rw_seccoder_universal.json

  # 或写成一行：
  python experiments/related_work/rw_des_protocol_eval.py score --inputs /path/to/seccoder_out/universal --arm seccoder_universal --no-canonical-whole-file -o experiments/rw_seccoder_universal.json

  # Windows cmd 续行用行尾 ^（**Linux/bash 下 ^ 会当作普通参数，导致报错**）
  #   python ... score --inputs D:\\seccoder_out\\universal ^
  #     --arm seccoder_universal --no-canonical-whole-file -o experiments\\rw_seccoder_universal.json

  # 一条命令串联 export → 你的生成子进程 → score，见 ``run_rw_baseline_pipeline.py``。
  # 各外部基线一键脚本（bash，在仓库根执行）：``run_rw_sven_des.sh``、``run_rw_selfrefine_des.sh``、
  # ``run_rw_seccoder_des.sh``、``run_rw_agentcoder_des.sh``；指标汇总表：``rw_aggregate_rates.py``。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import logging

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if TYPE_CHECKING:
    from utils.code_tester import CodeTester  # noqa: E402
    from utils.code_validator import CodeValidator  # noqa: E402
    from utils.test_data_loader import TestDataLoader  # noqa: E402

DES = "DES"
MODES = ("ECB", "CBC", "CFB", "OFB")
LANGS = ("python", "c", "cpp")
LANG_EXT = {"python": ".py", "c": ".c", "cpp": ".cpp"}


def _quiet_logs() -> None:
    logging.getLogger().setLevel(logging.WARNING)
    for name in ("CryptoAgent", "agent", "utils"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _expected_filename(mode: str, language: str) -> str:
    return f"des_{mode.lower()}{LANG_EXT[language]}"


def _build_export_rows() -> List[Dict[str, Any]]:
    try:
        from utils.test_data_loader import TestDataLoader
    except ModuleNotFoundError as e:
        if getattr(e, "name", "") == "yaml" or "yaml" in str(e).lower():
            print(
                "[rw_des_protocol_eval] 缺少 PyYAML，请执行: pip install pyyaml",
                file=sys.stderr,
            )
        raise
    loader = TestDataLoader()
    rows: List[Dict[str, Any]] = []
    for mode in MODES:
        td = loader.get_test_data(DES, mode)
        if not td:
            raise RuntimeError(f"缺少 test_data.yaml 中 DES/{mode} 的测试数据")
        for lang in LANGS:
            fn = _expected_filename(mode, lang)
            task_id = f"{DES}_{mode}_{lang}"
            scoring_en = (
                "Evaluation-only (aicrypto-helper CodeTester): final saved code should read "
                "hex strings from env TEST_PLAINTEXT, TEST_KEY, and TEST_IV (when mode needs IV), "
                "and produce ciphertext hex matching expected_ciphertext_hex. "
                "Do not use this contract as the model prompt: generate using each baseline's official "
                "pipeline and models only."
            )
            scoring_zh = (
                "【仅评测约定】若在本仓库运行 CodeTester：产物代码需从环境变量读取 TEST_PLAINTEXT / "
                "TEST_KEY / TEST_IV（十六进制字符串），并输出与 expected_ciphertext_hex 一致的密文十六进制。"
                "【生成阶段】须在各基线官方仓库内用其自有模型与脚本生成，勿使用本项目的 PromptLoader / CryptoAgent。"
            )
            rows.append(
                {
                    "task_id": task_id,
                    "algorithm": DES,
                    "mode": mode,
                    "language": lang,
                    "expected_filename": fn,
                    "operation": "加密解密",
                    "plaintext_hex": td.get("plaintext"),
                    "key_hex": td.get("key"),
                    "iv_hex": td.get("iv"),
                    "expected_ciphertext_hex": td.get("expected_ciphertext"),
                    "scoring_io_contract_en": scoring_en,
                    "scoring_io_contract_zh": scoring_zh,
                }
            )
    return rows


def cmd_export(args: argparse.Namespace) -> int:
    _quiet_logs()
    rows = _build_export_rows()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[rw_des_protocol_eval] 已写入 {len(rows)} 条任务 → {out}", file=sys.stderr)
    return 0


def _metrics(gsr: bool, vpr: bool, ftpr: bool) -> Dict[str, Any]:
    return {"gsr": gsr, "vpr": vpr, "ftpr": ftpr}


def _score_one(
    code: str,
    mode: str,
    language: str,
    *,
    loader: Any,
    validator: Any,
    tester: Any,
    allow_canonical: bool,
) -> Tuple[bool, bool, bool, Optional[str], Optional[str]]:
    test_data = loader.get_test_data(DES, mode)
    if not test_data:
        return False, False, False, "无测试数据", None

    vd = dict(test_data)
    vd["algorithm"] = DES
    vd["mode"] = mode

    v_ok, v_msg = validator.validate(
        code,
        language,
        vd,
        allow_canonical_whole_file=allow_canonical,
    )
    gsr = len(code.strip()) > 30
    if not v_ok:
        return gsr, False, False, v_msg if isinstance(v_msg, str) else str(v_msg), None

    plaintext = test_data.get("plaintext")
    expected_ciphertext = test_data.get("expected_ciphertext")
    key = test_data.get("key")
    iv = test_data.get("iv")

    t_ok, t_msg, details = tester.test(
        code,
        language,
        plaintext=plaintext,
        expected_ciphertext=expected_ciphertext,
        key=key,
        iv=iv,
        algorithm=DES,
        mode=mode,
        allow_canonical_whole_file=allow_canonical,
    )
    ftpr = bool(t_ok)
    hint = None if t_ok else (t_msg if isinstance(t_msg, str) else str(t_msg))
    return gsr, True, ftpr, None, hint


def cmd_score(args: argparse.Namespace) -> int:
    try:
        from utils.code_tester import CodeTester
        from utils.code_validator import CodeValidator
        from utils.test_data_loader import TestDataLoader
    except ModuleNotFoundError as e:
        if getattr(e, "name", "") == "yaml" or "yaml" in str(e).lower():
            print(
                "[rw_des_protocol_eval] 缺少 PyYAML，请执行: pip install pyyaml",
                file=sys.stderr,
            )
        raise
    _quiet_logs()
    root = Path(args.inputs)
    if root.exists() and not root.is_dir():
        print(f"[rw_des_protocol_eval] --inputs 不是目录: {root}", file=sys.stderr)
        return 2
    if not root.exists():
        if args.mkdir_inputs:
            root.mkdir(parents=True, exist_ok=True)
            print(
                f"[rw_des_protocol_eval] 已创建目录（12 个文件将先全部记为缺失）: {root.resolve()}",
                file=sys.stderr,
            )
        else:
            print(f"[rw_des_protocol_eval] 目录不存在: {root}", file=sys.stderr)
            print(
                f"[rw_des_protocol_eval] 请先: mkdir -p {root.resolve()}",
                file=sys.stderr,
            )
            print(
                "[rw_des_protocol_eval] 或重跑并加: --mkdir-inputs（创建空目录后直接写出 JSON，便于占位）",
                file=sys.stderr,
            )
            print(
                "[rw_des_protocol_eval] 再在目录内放入 12 个约定文件（见 experiments/rw_des_tasks.jsonl），"
                "或 experiments/related_work/seccoder_des_glue_generate.py --out-dir …；"
                "一键检查+打分见 run_rw_seccoder_des.sh。",
                file=sys.stderr,
            )
            return 2
    if not root.is_dir():
        print(f"[rw_des_protocol_eval] 不是目录: {root}", file=sys.stderr)
        return 2

    allow_canonical = not args.no_canonical_whole_file
    arm = args.arm or "baseline"
    loader = TestDataLoader()
    validator = CodeValidator()
    tester = CodeTester()
    results: List[Dict[str, Any]] = []
    for mode in MODES:
        for lang in LANGS:
            fn = _expected_filename(mode, lang)
            path = root / fn
            task_id = f"{DES}_{mode}_{lang}"
            if not path.is_file():
                results.append(
                    {
                        "task_id": task_id,
                        "mode": mode,
                        "language": lang,
                        "file": str(path),
                        "missing": True,
                        **_metrics(False, False, False),
                        "validation_hint": "文件不存在",
                        "test_hint": None,
                    }
                )
                continue
            code = path.read_text(encoding="utf-8", errors="replace")
            gsr, vpr, ftpr, v_hint, t_hint = _score_one(
                code,
                mode,
                lang,
                loader=loader,
                validator=validator,
                tester=tester,
                allow_canonical=allow_canonical,
            )
            results.append(
                {
                    "task_id": task_id,
                    "mode": mode,
                    "language": lang,
                    "file": str(path),
                    "missing": False,
                    **_metrics(gsr, vpr, ftpr),
                    "validation_hint": v_hint,
                    "test_hint": t_hint,
                }
            )

    n = len(results)
    rates = {
        "GSR": sum(1 for r in results if r["gsr"]) / n * 100.0,
        "VPR": sum(1 for r in results if r["vpr"]) / n * 100.0,
        "FTPR": sum(1 for r in results if r["ftpr"]) / n * 100.0,
    }
    payload: Dict[str, Any] = {
        "protocol": "DES_12_grid_ECB_CBC_CFB_OFB_x_python_c_cpp",
        "arm": arm,
        "inputs_dir": str(root.resolve()),
        "rates_percent": rates,
        "rows": results,
    }

    if args.output:
        outp = Path(args.output)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with open(outp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[rw_des_protocol_eval] 已写入 JSON → {outp}", file=sys.stderr)

    # stdout：简短 Markdown 行，便于粘到论文「实测」表
    print(f"### DES 相关工作口径评测 arm=`{arm}`（目录 `{root}`）\n")
    print(f"| GSR | VPR | FTPR |")
    print(f"| --- | --- | --- |")
    print(f"| {rates['GSR']:.2f} | {rates['VPR']:.2f} | {rates['FTPR']:.2f} |\n")
    miss = sum(1 for r in results if r.get("missing"))
    if miss:
        print(f"* 缺失文件数: {miss}/12（请将基线生成代码按 `des_<mode>.<ext>` 命名放入目录）*\n")

    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="DES 12 格相关工作对比（导出任务 / 打分外部目录）",
        epilog=(
            "换行续写：bash 用行尾反斜杠 \\ ；Windows cmd 用行尾 ^ 。"
            "在 Linux 上误用 ^ 会出现 unrecognized arguments: ^ 。"
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ex = sub.add_parser("export", help="导出 JSONL 任务列表")
    p_ex.add_argument("-o", "--output", default="experiments/rw_des_tasks.jsonl")
    p_ex.set_defaults(func=cmd_export)

    p_sc = sub.add_parser("score", help="对目录内 des_<mode>.py|c|cpp 打分")
    p_sc.add_argument(
        "--inputs",
        required=True,
        type=Path,
        help="包含 12 个源码文件的目录（填绝对/相对路径；勿使用文档里的 path/to 占位符）",
    )
    p_sc.add_argument(
        "--arm",
        default="seccoder_universal",
        help="记录用标签，如 universal / acl_rag / seccoder_universal",
    )
    p_sc.add_argument("-o", "--output", type=Path, default=None, help="另存 JSON 汇总")
    p_sc.add_argument(
        "--no-canonical-whole-file",
        action="store_true",
        help="关闭 C/C++ canonical OpenSSL 整文件替换（默认开启，与 Web 全量 golden 路径对齐）",
    )
    p_sc.add_argument(
        "--mkdir-inputs",
        action="store_true",
        help="当 --inputs 路径不存在时自动 mkdir -p 再打分（12 个文件均缺失也会写出 JSON）",
    )
    p_sc.set_defaults(func=cmd_score)

    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
