#!/usr/bin/env python3
"""
四类密码生成错误（论文 tab:error_repair 口径）演示与日志扩展：

1. **合成样例**：内置「基准 4 条」+「扩展若干条」（更多实验数据）；
2. **llm_performance 汇总**：从 ``llm_performance.json`` 读取未通过记录，按 ``classify_record`` 统计并抽样列表（捡日志里没通过的）；
3. **历史库重放**：从 ``code_history.db`` 读取 ``test_success=0`` 或 ``validation_success=0`` 的 Python 记录，对源码做 ``apply_common_quickfixes`` 后复测向量（不调用 LLM）。

用法：
  python experiments/error_category_repair_demo.py
  python experiments/error_category_repair_demo.py --verbose
  python experiments/error_category_repair_demo.py --skip-canonical     # 仅扩展合成 + 汇总 + 重放
  python experiments/error_category_repair_demo.py --summarize-performance ./llm_performance.json
  python experiments/error_category_repair_demo.py --replay-db-failures 8
  python experiments/error_category_repair_demo.py --probe-generated-python   # 扫描 generated_code/*.py 做向量+quickfix 对比
  python experiments/error_category_repair_demo.py --repair-performance-python --performance-json ./llm_performance.json
  python experiments/error_category_repair_demo.py --only-repair-performance   # 仅上表：日志 Python 失败 × 磁盘 py 的 quickfix 复测
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.error_repair_aggregate import (  # noqa: E402
    classify_record,
    flatten_performance_records,
)
from utils.code_tester import CodeTester  # noqa: E402
from utils.llm_code_quickfix import apply_common_quickfixes  # noqa: E402
from utils.test_data_loader import TestDataLoader  # noqa: E402

DEFAULT_PERF_JSON = ROOT / "llm_performance.json"
GENERATED_DIR = ROOT / "generated_code"

# generated_code 下常见文件名 -> (algorithm, mode)，用于磁盘上「最近一次生成」的探针（非日志里的失败快照）
_FILENAME_TO_TASK: Dict[str, Tuple[str, Optional[str]]] = {
    "des_ecb": ("DES", "ECB"),
    "des_cbc": ("DES", "CBC"),
    "des_cfb": ("DES", "CFB"),
    "des_ofb": ("DES", "OFB"),
    "aes_ecb": ("AES", "ECB"),
    "aes_cbc": ("AES", "CBC"),
    "aes_cfb": ("AES", "CFB"),
    "aes_ofb": ("AES", "OFB"),
    "aes_gcm": ("AES", "GCM"),
    "aes_ctr": ("AES", "CTR"),
    "sm4_ecb": ("SM4", "ECB"),
    "sm4_cbc": ("SM4", "CBC"),
    "sm4_cfb": ("SM4", "CFB"),
    "sm4_ofb": ("SM4", "OFB"),
}


def _vec_for(algorithm: str, mode: Optional[str]) -> Dict[str, Optional[str]]:
    td = TestDataLoader().get_test_data(algorithm.upper(), mode)
    assert td and td.get("expected_ciphertext"), f"无测试数据 {algorithm} {mode}"
    return {
        "plaintext": td["plaintext"],
        "expected_ciphertext": td["expected_ciphertext"],
        "key": td["key"],
        "iv": td.get("iv"),
    }


def _run_test(
    tester: CodeTester,
    code: str,
    algorithm: str,
    mode: Optional[str],
    language: str = "python",
) -> Tuple[bool, str]:
    v = _vec_for(algorithm, mode)
    ok, msg, _ = tester.test(
        code,
        language,
        plaintext=v["plaintext"],
        expected_ciphertext=v["expected_ciphertext"],
        key=v["key"],
        iv=v.get("iv"),
        algorithm=algorithm.upper(),
        mode=mode,
    )
    return ok, msg


def _classify_from_run(
    ok: bool, err_snippet: str, algorithm: str, mode: Optional[str]
) -> str:
    rec = {
        "error_message": None if ok else err_snippet[:1200],
        "algorithm": algorithm,
        "mode": mode or "",
        "test_success": ok,
        "validation_success": True,
    }
    return classify_record(rec)


# --- 参考实现（DES-ECB / DES-OFB）---

DES_ECB_GOOD = r"""import os
import binascii
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
    pt = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    raw = pad(pt, DES.block_size)
    c = DES.new(key, DES.MODE_ECB)
    ct = c.encrypt(raw)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
"""

DES_OFB_GOOD = r"""import os
import binascii
from Crypto.Cipher import DES

def main():
    pt = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    iv = binascii.unhexlify(os.environ["TEST_IV"])
    cipher = DES.new(key, DES.MODE_OFB, iv=iv)
    ct = cipher.encrypt(pt)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
"""


# ========== 基准四类（带严格分类断言）==========

BROKEN_ENV = r'''import binasci
import os
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
    pt = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    raw = pad(pt, DES.block_size)
    c = DES.new(key, DES.MODE_ECB)
    ct = c.encrypt(raw)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
'''


def repair_env_typo_binasci(code: str) -> str:
    code = apply_common_quickfixes(code, "python")
    return code.replace("import binasci\n", "import binascii\n")


BROKEN_STRUCT = r'''import os
import binascii
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
print("此处故意顶格：触发 SyntaxError")

if __name__ == "__main__":
    main()
'''


def repair_to_ecb_good(_: str) -> str:
    return apply_common_quickfixes(DES_ECB_GOOD, "python")


BROKEN_ALGO = r'''import os
import binascii
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
    raise RuntimeError("DES密钥必须为8字节：当前从环境读取后尺度不符合分组密码要求")
    pt = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    raw = pad(pt, DES.block_size)
    c = DES.new(key, DES.MODE_ECB)
    ct = c.encrypt(raw)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
'''


BROKEN_IMPL_XOR = r'''import os
import binascii
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
    pt = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    pt = bytes(b ^ 1 for b in pt)
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    raw = pad(pt, DES.block_size)
    c = DES.new(key, DES.MODE_ECB)
    ct = c.encrypt(raw)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
'''


CANONICAL_CASES: List[Tuple[str, str, Optional[str], str, Callable[[str], str], str, str]] = [
    ("环境配置-错拼 binasci", "DES", "ECB", BROKEN_ENV, repair_env_typo_binasci, "改正 ``import binasci``", "环境配置"),
    ("代码结构-顶格语句", "DES", "ECB", BROKEN_STRUCT, repair_to_ecb_good, "整段替换为 DES-ECB 参考实现", "代码结构"),
    ("算法理解-密钥尺度异常", "DES", "ECB", BROKEN_ALGO, repair_to_ecb_good, "整段替换为 DES-ECB 参考实现", "算法理解"),
    ("实现细节-明文异或", "DES", "ECB", BROKEN_IMPL_XOR, repair_to_ecb_good, "整段替换为 DES-ECB 参考实现", "实现细节"),
]


# ========== 扩展合成（更多实验数据；分类列仅供参考，不强制等于某一类）==========

# 缺 import os / 缺 Cipher（由 quickfix 补）
BROKEN_ENV_MISSING_IMPORTS = r'''import binascii
from Crypto.Util.Padding import pad

def main():
    pt = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    raw = pad(pt, DES.block_size)
    c = DES.new(key, DES.MODE_ECB)
    ct = c.encrypt(raw)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
'''


def repair_quickfix_only(code: str) -> str:
    return apply_common_quickfixes(code, "python")


BROKEN_STRUCT2 = r'''import os
import binascii
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
    x = (1 + 2

if __name__ == "__main__":
    main()
'''


BROKEN_ALGO_PADDING_MSG = r'''import os
import binascii
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
    raise RuntimeError("padding 与 block size 在本任务中与 ECB 填充不一致")
    pt = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    raw = pad(pt, DES.block_size)
    c = DES.new(key, DES.MODE_ECB)
    ct = c.encrypt(raw)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
'''


# OFB 向量测试下误用 ECB → 密文不匹配（实现/模式混淆类）
BROKEN_OFB_BUT_ECB = r"""import os
import binascii
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

def main():
    pt = binascii.unhexlify(os.environ["TEST_PLAINTEXT"])
    key = binascii.unhexlify(os.environ["TEST_KEY"])
    raw = pad(pt, DES.block_size)
    c = DES.new(key, DES.MODE_ECB)
    ct = c.encrypt(raw)
    print("密文:", binascii.hexlify(ct).decode().upper())

if __name__ == "__main__":
    main()
"""


def repair_to_ofb_good(_: str) -> str:
    return apply_common_quickfixes(DES_OFB_GOOD, "python")


EXTENDED_CASES: List[Tuple[str, str, Optional[str], str, Callable[[str], str], str, Optional[str]]] = [
    (
        "扩展-缺 import/os/Cipher",
        "DES",
        "ECB",
        BROKEN_ENV_MISSING_IMPORTS,
        repair_quickfix_only,
        "仅 ``apply_common_quickfixes``（补 os、DES、binascii 等）",
        None,
    ),
    ("扩展-括号未闭合", "DES", "ECB", BROKEN_STRUCT2, repair_to_ecb_good, "语法错误 → 参考 ECB 整段替换", "代码结构"),
    (
        "扩展-padding/块长文案异常",
        "DES",
        "ECB",
        BROKEN_ALGO_PADDING_MSG,
        repair_to_ecb_good,
        "触发 algo 关键词后仍以参考 ECB 替换",
        "算法理解",
    ),
    (
        "扩展-OFB任务误写ECB",
        "DES",
        "OFB",
        BROKEN_OFB_BUT_ECB,
        repair_to_ofb_good,
        "OFB 向量下改为 MODE_OFB + 无填充直连 encrypt(pt)",
        None,
    ),
]


def _print_case_table(
    tester: CodeTester,
    cases: List[Tuple[str, str, Optional[str], str, Callable[[str], str], str, Optional[str]]],
    *,
    strict_cat: bool,
    title: str,
) -> None:
    print(f"\n## {title}\n")
    print("| 样例 | 算法 | 模式 | 修复前通过 | 启发式分类 | 修复后通过 | 说明 |")
    print("| :--- | :--- | :--- | :---: | :--- | :---: | :--- |")
    for title_, algo, mode, broken, repair_fn, note, expect in cases:
        ok0, msg0 = _run_test(tester, broken, algo, mode)
        if strict_cat:
            assert not ok0, f"[{title_}] 损坏样例应未通过"
        cat0 = _classify_from_run(ok0, msg0, algo, mode or "")
        if strict_cat and expect:
            assert cat0 == expect, (
                f"[{title_}] 期望分类 {expect}，实际 {cat0}：{msg0[:180]}"
            )

        fixed = repair_fn(broken)
        ok1, msg1 = _run_test(tester, fixed, algo, mode)
        if strict_cat:
            assert ok1, f"[{title_}] 修复后应通过：{msg1}"

        hint = (msg0.replace("\n", " ")[:72] + "…") if msg0 and len(msg0) > 72 else (msg0 or "—")
        print(
            f"| {title_} | {algo} | {mode or '—'} | {'是' if ok0 else '否'} | {cat0} | "
            f"{'是' if ok1 else '否'} | {note} |"
        )


def _disk_py_for_perf_record(r: Dict) -> Optional[Path]:
    """根据性能日志字段推断 ``generated_code`` 下文件名（存在则返回路径）。"""
    algo = (r.get("algorithm") or "").strip().upper()
    mode = (r.get("mode") or "").strip()
    if not algo:
        return None
    if algo == "RSA":
        fp = GENERATED_DIR / "rsa.py"
        return fp if fp.is_file() else None
    if not mode:
        return None
    fp = GENERATED_DIR / f"{algo.lower()}_{mode.lower()}.py"
    return fp if fp.is_file() else None


def repair_performance_python_failures(tester: CodeTester, perf_path: Path) -> None:
    """
    将 ``llm_performance.json`` 中 **language=python 且失败** 的任务与磁盘
    ``generated_code/{algorithm}_{mode}.py`` 对齐，对当前文件做 ``apply_common_quickfixes`` 后复测。

    说明：JSON **不含当时源码**，磁盘文件是**最新一次生成**，可能与某条失败记录不完全对应；
    本段回答「若仅缺 import 类错误，当前磁盘版本能否被 quickfix 救回」。
    """
    if not perf_path.is_file():
        print(f"\n（跳过）未找到性能日志: {perf_path}\n")
        return
    if not GENERATED_DIR.is_dir():
        print(f"\n（跳过）无目录 {GENERATED_DIR}\n")
        return

    raw = json.loads(perf_path.read_text(encoding="utf-8"))
    flat = flatten_performance_records(raw)

    def _fail(r: Dict) -> bool:
        return r.get("validation_success") is False or r.get("test_success") is False

    py_fails = [
        r
        for r in flat
        if _fail(r) and (r.get("language") or "").lower() == "python"
    ]

    fp_counts: Dict[Path, int] = defaultdict(int)
    fp_sample_msg: Dict[Path, str] = {}
    missing_pairs: List[Tuple[str, str]] = []

    for r in py_fails:
        fp = _disk_py_for_perf_record(r)
        algo = (r.get("algorithm") or "").strip().upper()
        mode = (r.get("mode") or "").strip() or "—"
        if fp is None:
            missing_pairs.append((algo, mode))
            continue
        fp_counts[fp] += 1
        if fp not in fp_sample_msg:
            em = (r.get("error_message") or "").strip()
            fp_sample_msg[fp] = em.replace("\n", " ")[:120]

    print(f"\n## 针对 llm_performance 中 Python 失败 → 磁盘快照 quickfix 复测（{perf_path.name}）\n")
    print(f"- Python 失败记录 **{len(py_fails)}** 条；能在 ``generated_code`` 找到同名文件的 **{len(fp_counts)}** 个；")
    print(f"- 找不到对应文件的失败组合 **{len(missing_pairs)}** 条（多为无 mode、或未生成过该 py）。\n")

    if not fp_counts:
        print("*无任何可对齐的 .py 文件，无法做 quickfix 复测。*\n")
        return

    print("| 文件 | 关联失败条数 | 启发式分类(日志) | 修复前通过 | quickfix 后通过 | 说明 |")
    print("| :--- | :---: | :--- | :---: | :---: | :--- |")

    newly_ok = 0
    still_bad = 0
    already_ok = 0

    for fp in sorted(fp_counts.keys(), key=lambda p: p.name):
        task = _FILENAME_TO_TASK.get(fp.stem.lower())
        if not task:
            print(f"| {fp.name} | {fp_counts[fp]} | — | — | — | 未配置映射，跳过 |")
            continue
        algo_guess, mode_guess = task

        code = fp.read_text(encoding="utf-8")
        ok0, msg0 = _run_test(tester, code, algo_guess, mode_guess)
        fixed = apply_common_quickfixes(code, "python")
        ok1, msg1 = _run_test(tester, fixed, algo_guess, mode_guess)

        rec_cat = classify_record(
            {
                "error_message": fp_sample_msg.get(fp, ""),
                "algorithm": algo_guess,
                "mode": mode_guess or "",
                "test_success": False,
                "validation_success": True,
            }
        )

        if ok0:
            note = "磁盘当前已通过（日志失败或为旧记录）"
            already_ok += 1
        elif ok1:
            note = "quickfix 后通过（缺 import 等可救）"
            newly_ok += 1
        else:
            note = "仍失败（语义/算法/校验逻辑等非 import）"
            still_bad += 1

        print(
            f"| {fp.name} | {fp_counts[fp]} | {rec_cat} | {'是' if ok0 else '否'} | "
            f"{'是' if ok1 else '否'} | {note} |"
        )

    print(
        f"\n*小结：唯一文件数 **{len(fp_counts)}** — 修复前已通过 **{already_ok}**，"
        f"仅 quickfix 后由否转是 **{newly_ok}**，仍失败 **{still_bad}**。"
        f" 其余 Python 失败若无对应 py 或未命中 import 类，需 LLM/人工或更新生成物。*\n"
    )


def summarize_performance(perf_path: Path, sample_rows: int = 20) -> None:
    if not perf_path.is_file():
        print(f"\n（跳过）未找到性能日志: {perf_path}\n")
        return
    raw = json.loads(perf_path.read_text(encoding="utf-8"))
    flat = flatten_performance_records(raw)

    def _fail(r: Dict) -> bool:
        return r.get("validation_success") is False or r.get("test_success") is False

    fails = [r for r in flat if _fail(r)]
    print(f"\n## llm_performance 失败明细聚合（{perf_path.name}）\n")
    print(f"- 扫描明细 **{len(flat)}** 条，失败 **{len(fails)}** 条。\n")

    hist = Counter(classify_record(r) for r in fails)
    print("| 启发式类型 | 条数 |")
    print("| :--- | :---: |")
    for k in ["算法理解", "实现细节", "环境配置", "代码结构"]:
        print(f"| {k} | {hist.get(k, 0)} |")
    print(f"| **合计** | **{len(fails)}** |")
    print("\n### 失败抽样（按日志顺序前若干条）\n")
    print("| 算法 | 模式 | 语言 | 分类 | error_message 摘录 |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for r in fails[:sample_rows]:
        cat = classify_record(r)
        ex = (r.get("error_message") or "—").replace("\n", " ")[:100]
        print(
            f"| {r.get('algorithm','')} | {r.get('mode') or '—'} | {r.get('language','')} | {cat} | {ex} |"
        )
    print(
        "\n*说明：此处仅**分类与摘录**，源码不在 JSON 内；确定性修补请用 ``--replay-db-failures`` 读库内失败代码。*"
    )


def probe_generated_python(tester: CodeTester) -> None:
    """对 ``generated_code/*.py`` 做向量测试；日志里若未持久化失败代码，可用磁盘最新生成物做对比实验。"""
    if not GENERATED_DIR.is_dir():
        print(f"\n（跳过）无目录 {GENERATED_DIR}\n")
        return
    py_files = sorted(GENERATED_DIR.glob("*.py"))
    print(f"\n## generated_code 内 Python 文件探针（共 {len(py_files)} 个）\n")
    print("| 文件 | 推断任务 | 修复前通过 | 启发式分类 | quickfix 后通过 | 备注 |")
    print("| :--- | :--- | :---: | :--- | :---: | :--- |")
    for fp in py_files:
        key = fp.stem.lower()
        task = _FILENAME_TO_TASK.get(key)
        if not task:
            print(f"| {fp.name} | — | — | — | — | 未配置映射，跳过 |")
            continue
        algo, mode = task
        try:
            code = fp.read_text(encoding="utf-8")
        except OSError as e:
            print(f"| {fp.name} | {algo} {mode or '—'} | — | — | — | 读取失败：{e} |")
            continue
        ok0, msg0 = _run_test(tester, code, algo, mode)
        cat0 = "—" if ok0 else _classify_from_run(False, msg0, algo, mode or "")
        fixed = apply_common_quickfixes(code, "python")
        ok1, msg1 = _run_test(tester, fixed, algo, mode)
        if ok0:
            note = "已通过"
        elif ok1:
            note = "quickfix 后通过"
        else:
            note = "仍失败（多为语义/算法错误，非 import quickfix）"
        print(
            f"| {fp.name} | {algo} {mode or '—'} | {'是' if ok0 else '否'} | {cat0} | "
            f"{'是' if ok1 else '否'} | {note} |"
        )


def replay_db_failures(tester: CodeTester, limit: int) -> None:
    db_path = ROOT / "code_history.db"
    if not db_path.is_file():
        print(f"\n（跳过）未找到 {db_path}\n")
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT id, algorithm, mode, language, code, test_success, validation_success, timestamp
        FROM code_history
        WHERE language = ?
          AND (IFNULL(test_success,1) = 0 OR IFNULL(validation_success,1) = 0)
        ORDER BY id DESC
        LIMIT ?
        """,
        ("python", limit),
    )
    rows = cur.fetchall()
    conn.close()

    print(f"\n## code_history 中 Python 失败记录重放（最多 {limit} 条，当前命中 {len(rows)} 条）\n")
    if not rows:
        print(
            "*库中若无失败行，多为生成仅在「测试未通过时不写历史」策略下未持久化；可看合成样例与 llm_performance 分类。*\n"
        )
        return

    print("| id | 算法 | 模式 | 修复前通过 | 分类(依据本次 stderr/比对) | quickfix 后通过 | 备注 |")
    print("| :--- | :--- | :--- | :---: | :--- | :---: | :--- |")
    for row in rows:
        rid = row["id"]
        algo = (row["algorithm"] or "DES").upper()
        mode = row["mode"]
        code = row["code"]
        if algo == "RSA":
            print(f"| {rid} | {algo} | — | — | — | — | 跳过 RSA（向量口径不同） |")
            continue
        if not mode:
            mode = "ECB"

        ok0, msg0 = _run_test(tester, code, algo, mode)
        cat0 = _classify_from_run(ok0, msg0, algo, mode)

        fixed = apply_common_quickfixes(code, "python")
        ok1, msg1 = _run_test(tester, fixed, algo, mode)

        if ok0:
            note = "库里标记失败但本次向量已通过（代码或环境已变）"
        elif ok1:
            note = "quickfix 后向量通过"
        else:
            note = f"quickfix 仍失败（需 LLM/人工） {(msg1 or '')[:48]}"

        print(
            f"| {rid} | {algo} | {mode} | {'是' if ok0 else '否'} | {cat0} | "
            f"{'是' if ok1 else '否'} | {note} |"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="错误分类 + 确定性修补演示 / 日志汇总 / 库内重放")
    ap.add_argument("--verbose", action="store_true", help="打印 CryptoAgent/CodeTester 详细日志")
    ap.add_argument("--skip-canonical", action="store_true", help="不跑基准四类，仅扩展与汇总")
    ap.add_argument("--performance-json", type=Path, default=None, help=f"默认 {DEFAULT_PERF_JSON}")
    ap.add_argument("--no-performance-summary", action="store_true", help="不打印 llm_performance 汇总")
    ap.add_argument("--replay-db-failures", type=int, default=0, metavar="N", help="重放库内 Python 失败代码条数")
    ap.add_argument("--summarize-performance-only", action="store_true", help="仅输出 llm_performance 分类汇总后退出")
    ap.add_argument(
        "--probe-generated-python",
        action="store_true",
        help="扫描 generated_code/*.py：向量测试 + apply_common_quickfixes 前后对比（映射见脚本内 _FILENAME_TO_TASK）",
    )
    ap.add_argument(
        "--repair-performance-python",
        action="store_true",
        help="读取 llm_performance 中 Python 失败项，按算法/模式对齐 generated_code 下 .py 做 quickfix 前后对比",
    )
    ap.add_argument(
        "--only-repair-performance",
        action="store_true",
        help="只执行「Python 失败 × 磁盘 quickfix 复测」（等价于打开 --repair-performance-python 且不跑合成表）",
    )
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    if not args.verbose:
        logging.getLogger("CryptoAgent").setLevel(logging.ERROR)

    perf_path = args.performance_json or DEFAULT_PERF_JSON

    if args.summarize_performance_only:
        summarize_performance(perf_path)
        return

    if args.only_repair_performance:
        args.repair_performance_python = True

    tester = CodeTester()

    if args.only_repair_performance:
        repair_performance_python_failures(tester, perf_path)
        print(
            "\n*说明：``llm_performance`` 无源码，此处用 **generated_code 当前文件** 代理；"
            "若磁盘中已是通过版，会显示「日志失败但磁盘已通过」。真正「未通过」需看仍失败行（如 import 也救不了）。*\n"
        )
        return

    if args.probe_generated_python:
        probe_generated_python(tester)
        return

    if not args.skip_canonical:
        _print_case_table(tester, CANONICAL_CASES, strict_cat=True, title="基准四类（分类断言 + 向量断言）")

    _print_case_table(tester, EXTENDED_CASES, strict_cat=False, title="扩展合成样例（更多实验数据）")

    if not args.no_performance_summary:
        summarize_performance(perf_path)

    if args.replay_db_failures > 0:
        replay_db_failures(tester, args.replay_db_failures)

    if args.repair_performance_python:
        repair_performance_python_failures(tester, perf_path)

    print(
        "\n*确定性修补链：`apply_common_quickfixes` + 本脚本中的参考实现替换；完整闭环另含 LLM `improve_code`。*\n"
    )


if __name__ == "__main__":
    main()
