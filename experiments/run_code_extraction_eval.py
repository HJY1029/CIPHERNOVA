#!/usr/bin/env python3
"""
## 4. 代码提取实验（论文 tab:extraction_accuracy）

复现论文「代码提取准确率与处理时间」表：对保存的 **LLM 原始输出** 文本（模型回复全文，
不是已经抠出来的纯代码文件）跑多级提取，统计准确率与耗时。

提取路径（与正文一致）：
- Markdown 围栏：`extract_code_markdown_fence_only`
- 纯文本识别：去围栏后 `extract_code_plain_text_recognition`
- 全文多级：`extract_code`（含启发式修复）

「一致率」：各路径提取结果与全文 `extract_code` 在规范化后是否一致。

### 数据准备

准备若干原始输出文本；可用任意扩展名，若扩展名无法推断语言则请传 `--language`。
建议：`.txt` 批量放目录，或 `.py`/`.c`/`.cpp` 等以便自动识别语言。

### 命令示例（复制即用）

默认 stdout 输出论文版式 Markdown 表；`-o` 写入文件；`--json-output` 另存明细 JSON。

```bash
# 不传 --glob：自动使用脚本内置的 LLM 风格原始输出样例（零依赖试跑）
python experiments/run_code_extraction_eval.py

# 批量评测自有语料
python experiments/run_code_extraction_eval.py --glob "data/extraction_corpus/**/*.txt"

# 写入 Markdown + 可选 JSON 明细 + booktabs LaTeX 片段
python experiments/run_code_extraction_eval.py -o extraction_stats.md --json-output extraction_stats.json \
  --latex-output extraction_stats_tab.tex

python experiments/run_code_extraction_eval.py --glob "data/extraction_corpus/**/*.txt" -o extraction_stats.md

# 扩展名无法区分语言时固定语言
python experiments/run_code_extraction_eval.py --glob "samples/*.txt" --language cpp -o extraction_stats.md

# 从 code_history.db 批量评测（落库后的 code 字段；口径见 --from-history 说明）
python experiments/run_code_extraction_eval.py --from-history \
  -o extraction_stats.md --json-output extraction_stats.json

python experiments/run_code_extraction_eval.py --from-history --test-success-only \
  --dedupe-slot --provider deepseek -o extraction_stats.md

# 同时导出语料目录，供后续 --glob 复跑
python experiments/run_code_extraction_eval.py --from-history \
  --export-corpus data/extraction_corpus/from_history
```

说明：本脚本 **不调用 LLM**，仅在本地跑提取函数。不传 `--glob` / `--from-history` 时默认 **12** 条内置样例（围栏 / 夹代码叙述）。

**`--from-history` 口径**：默认将 `code_history.code` **包装为带 Markdown 围栏的 LLM 式回复**（`--wrap-as-fence`，可 `--no-wrap-as-fence` 恢复纯代码幂等评测），并合并内置围栏样例，使 Markdown 路径有有效准确率；同时追加 **`BUILTIN_LLM_RAW_SAMPLES`**（可用 `--no-builtin-fence-samples` 关闭）。
「整体」行平均耗时 = 三策略各行平均耗时的**等权算术平均**（非三行数值相加）。
"""
from __future__ import annotations

import argparse
import glob as glob_mod
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.experiment_outputs import resolve_under_results  # noqa: E402
from utils.history_manager import HistoryManager  # noqa: E402
from agent.code_processing import (  # noqa: E402
    extract_code,
    extract_code_markdown_fence_only,
    extract_code_plain_text_recognition,
)

# 内置「LLM 原始回复」风格样例（非纯代码文件）：不传 --glob 时默认用其跑 tab:extraction_accuracy 口径
BUILTIN_LLM_RAW_SAMPLES: List[Tuple[str, str, str]] = [
    (
        "builtin:python_markdown_fence",
        "python",
        """Here is a minimal DES helper in Python.

```python
def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))

def main():
    print(xor_bytes(b"\\x01\\x02", b"\\x02\\x01").hex())

if __name__ == "__main__":
    main()
```

Let me know if you need CBC mode as well.
""",
    ),
    (
        "builtin:python_plain_mixed",
        "python",
        """The following program validates hex input and prints OK.

```python
def run():
    s = "deadbeef"
    if len(s) % 2 != 0:
        raise ValueError("bad hex")
    print("ok")

run()
```

End of answer.
""",
    ),
    (
        "builtin:c_markdown_fence",
        "c",
        """Sure — here is a self-contained C snippet.

```c
#include <stdio.h>
#include <stdint.h>

static void print_u8(uint8_t x) {
    printf("%02x", x);
}

int main(void) {
    print_u8(0x3a);
    return 0;
}
```

Compile with gcc.
""",
    ),
    (
        "builtin:cpp_markdown_fence",
        "cpp",
        """Below is C++17 code using std::vector.

```cpp
#include <iostream>
#include <vector>

int main() {
    std::vector<int> v = {1, 2, 3};
    std::cout << v.size() << std::endl;
    return 0;
}
```
""",
    ),
    (
        "builtin:python_fence_language_tag",
        "python",
        """```Python
def add(a, b):
    return a + b
```
""",
    ),
    (
        "builtin:python_gmssl_stub",
        "python",
        """用 gmssl 的示例（标准围栏）：

```python
from gmssl import sm4

def enc_block(k, p):
    return sm4.crypt_ecb(k, p, 1)

if __name__ == "__main__":
    pass
```
""",
    ),
    (
        "builtin:python_pycryptodome_aes",
        "python",
        """```python
from Crypto.Cipher import AES
KEY = b"0123456789abcdef"
cipher = AES.new(KEY, AES.MODE_ECB)
```
""",
    ),
    (
        "builtin:python_triple_quoted",
        "python",
        """```python
s = "a\\nb"
print(len(s))
```
""",
    ),
    (
        "builtin:c_openssl_minimal",
        "c",
        """Descriptive text before code.

```c
#include <stdio.h>
int main(void) { puts("ok"); return 0; }
```

Compile with gcc.
""",
    ),
    (
        "builtin:c_uint8_snippet",
        "c",
        """```c
#include <stdint.h>
#include <stdio.h>
int main(void) {
    uint8_t x = 0xff;
    (void)x;
    return 0;
}
```
""",
    ),
    (
        "builtin:cpp_iostream_min",
        "cpp",
        """参考实现如下。

```cpp
#include <iostream>
int main() { std::cout << 1; return 0; }
```

""",
    ),
    (
        "builtin:cpp_vector_stub",
        "cpp",
        """```cpp
#include <vector>
int main() { std::vector<int> v{1}; return 0; }
```
""",
    ),
]


def _norm(s: str) -> str:
    return " ".join((s or "").split())


_FENCE_LANG_TAG = {"python": "python", "c": "c", "cpp": "cpp"}


def _wrap_as_fenced_llm_response(code: str, lang: str) -> str:
    """将已提取源码包装成带 Markdown 围栏的 LLM 式回复，供 Markdown 路径评测。"""
    tag = _FENCE_LANG_TAG.get(lang.lower(), lang.lower())
    body = (code or "").rstrip()
    return (
        f"Below is the {tag} implementation:\n\n"
        f"```{tag}\n"
        f"{body}\n"
        f"```\n"
    )


def _lang_from_path(p: Path, override: Optional[str]) -> str:
    if override:
        return override.lower()
    suf = p.suffix.lower()
    if suf == ".py":
        return "python"
    if suf in (".c", ".h"):
        return "c"
    if suf in (".cpp", ".cc", ".cxx", ".hpp"):
        return "cpp"
    return "python"


def _gather_files(pattern: str) -> List[Path]:
    files = sorted(Path(p).resolve() for p in glob_mod.glob(pattern, recursive=True))
    return [f for f in files if f.is_file()]


def _fmt_pct_cell(x: float) -> str:
    """论文表风格：整数百分比不带小数尾。"""
    if abs(x - round(x)) < 1e-6:
        return str(int(round(x)))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _fmt_ms_cell(x: float) -> str:
    return f"{x:.2f}"


def _metrics_for_file(raw: str, lang: str) -> Dict[str, Any]:
    full_ref = extract_code(raw, lang)
    md = extract_code_markdown_fence_only(raw, lang)
    plain = extract_code_plain_text_recognition(raw, lang)

    t0 = time.perf_counter()
    extract_code_markdown_fence_only(raw, lang)
    t_md = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    extract_code_plain_text_recognition(raw, lang)
    t_plain = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    extract_code(raw, lang)
    t_full = (time.perf_counter() - t0) * 1000

    return {
        "md_match": _norm(md) == _norm(full_ref),
        "plain_match": _norm(plain) == _norm(full_ref),
        "full_nonempty": bool(full_ref.strip()),
        "t_md_ms": t_md,
        "t_plain_ms": t_plain,
        "t_full_ms": t_full,
        "ref_len": len(full_ref),
    }


def _lang_from_record(language: Optional[str], override: Optional[str]) -> str:
    if override:
        return override.lower()
    lang = (language or "python").strip().lower()
    if lang in ("c++", "cxx"):
        return "cpp"
    return lang


def _history_label(rec: Dict[str, Any]) -> str:
    alg = (rec.get("algorithm") or "?").upper()
    mode = rec.get("mode") or ""
    lang = (rec.get("language") or "?").lower()
    prov = (rec.get("provider") or "?").lower()
    hid = rec.get("id") or "?"
    if alg == "RSA" or not str(mode).strip():
        return f"history:{prov}|{alg}|RSA|{lang}|id{hid}"
    return f"history:{prov}|{alg}|{str(mode).upper()}|{lang}|id{hid}"


def _load_history_samples(
    db_path: Path,
    *,
    provider: Optional[str],
    algorithm: Optional[str],
    language: Optional[str],
    test_success_only: bool,
    dedupe_slot: bool,
    limit: Optional[int],
) -> List[Tuple[str, str, str]]:
    """返回 (label, language, text) 列表。"""
    hm = HistoryManager(str(db_path))
    rows = hm.get_history(
        limit=None,
        reverse=True,
        algorithm=algorithm,
        language=language,
        provider=provider,
    )
    if test_success_only:
        rows = [r for r in rows if r.get("test_success")]
    if dedupe_slot:
        seen: set = set()
        deduped: List[Dict[str, Any]] = []
        for r in rows:
            key = HistoryManager.normalize_case_key(
                r.get("algorithm"), r.get("mode"), r.get("language")
            )
            prov = (r.get("provider") or "").strip().lower()
            slot = (prov, key[0], key[1], key[2])
            if slot in seen:
                continue
            seen.add(slot)
            deduped.append(r)
        rows = deduped
    if limit is not None and limit > 0:
        rows = rows[:limit]
    out: List[Tuple[str, str, str]] = []
    for rec in rows:
        code = rec.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        lang = _lang_from_record(rec.get("language"), None)
        out.append((_history_label(rec), lang, code))
    return out


def _export_corpus(samples: List[Tuple[str, str, str]], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    ext_map = {"python": ".py", "c": ".c", "cpp": ".cpp"}
    n = 0
    for label, lang, text in samples:
        ext = ext_map.get(lang, ".txt")
        safe = (
            label.replace("\\", "_")
            .replace("/", "_")
            .replace(":", "_")
            .replace("|", "_")
        )
        path = out_dir / f"{safe}{ext}"
        path.write_text(text, encoding="utf-8")
        n += 1
    return n


def _run_metrics_on_samples(
    samples: List[Tuple[str, str, str]],
    language_override: Optional[str],
) -> Tuple[List[Dict[str, Any]], float, float, float]:
    rows_detail: List[Dict[str, Any]] = []
    tot_md_t = tot_plain_t = tot_full_t = 0.0
    for label, lang, raw in samples:
        eff_lang = (language_override or lang).lower()
        m = _metrics_for_file(raw, eff_lang)
        rows_detail.append({"file": label, "language": eff_lang, **m})
        tot_md_t += m["t_md_ms"]
        tot_plain_t += m["t_plain_ms"]
        tot_full_t += m["t_full_ms"]
    return rows_detail, tot_md_t, tot_plain_t, tot_full_t


def main() -> None:
    ap = argparse.ArgumentParser(
        description="代码提取路径评测（论文 §4 / tab:extraction_accuracy；详见模块顶部文档）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python experiments/run_code_extraction_eval.py\n"
            '  python experiments/run_code_extraction_eval.py --glob "data/extraction_corpus/**/*.txt"\n'
            "  python experiments/run_code_extraction_eval.py --from-history -o extraction_stats.md\n"
            "  python experiments/run_code_extraction_eval.py -o extraction_stats.md --json-output extraction_stats.json\n"
            "不传 --glob/--from-history 时使用内置样例；不调用 LLM。"
        ),
    )
    ap.add_argument(
        "--from-history",
        action="store_true",
        help="从 code_history.db 读取 code 字段批量评测（已提取源码，非 LLM 原始回复）",
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite 路径（默认项目根 code_history.db）",
    )
    ap.add_argument("--provider", default=None, help="--from-history：按 provider 筛选")
    ap.add_argument("--algorithm", default=None, help="--from-history：按算法筛选")
    ap.add_argument(
        "--test-success-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="--from-history：仅 test_success=1（默认 true）",
    )
    ap.add_argument(
        "--wrap-as-fence",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="--from-history：将 code 包装为带 ``` 围栏的 LLM 式回复再评测（默认 true，使 Markdown 路径有样本）",
    )
    ap.add_argument(
        "--include-builtin-fence-samples",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="--from-history：合并内置 LLM 围栏原始回复样例（默认 true）",
    )
    ap.add_argument(
        "--dedupe-slot",
        action="store_true",
        help="--from-history：每个 provider×算法×模式×语言 仅保留最新一条",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="--from-history：最多评测 N 条（0=不限制）",
    )
    ap.add_argument(
        "--export-corpus",
        type=Path,
        default=None,
        help="--from-history：将读出的 code 导出到目录（扩展名按语言），便于后续 --glob",
    )
    ap.add_argument(
        "--glob",
        default=None,
        help=r'可选；glob 语料路径，如 data/extraction_corpus/**/*.txt。与 --from-history 互斥',
    )
    ap.add_argument("--language", default=None, help="固定语言；不设则按扩展名推断")
    ap.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="可选：写入 summary+detail 完整 JSON（机器处理）；终端与 -o 默认为论文版式 Markdown 表",
    )
    ap.add_argument("-o", "--output", type=Path, default=None)
    ap.add_argument(
        "--latex-output",
        type=Path,
        default=None,
        help="可选：写出 booktabs 三线表 LaTeX 片段（需 \\usepackage{booktabs}）",
    )
    args = ap.parse_args()

    if args.from_history and args.glob:
        print("--from-history 与 --glob 互斥，请只选一种数据来源。", file=sys.stderr)
        sys.exit(1)

    print(
        "[aicrypto-helper] 本脚本在本地跑代码提取算法，**不会调用 LLM**。",
        file=sys.stderr,
    )

    rows_detail: List[Dict[str, Any]] = []
    tot_md_t = tot_plain_t = tot_full_t = 0.0
    corpus_source = "builtin"
    history_meta: Dict[str, Any] = {}

    if args.from_history:
        db_path = args.db or (ROOT / "code_history.db")
        if not db_path.is_file():
            print(f"code_history 不存在: {db_path}", file=sys.stderr)
            sys.exit(1)
        lim = args.limit if args.limit and args.limit > 0 else None
        samples = _load_history_samples(
            db_path,
            provider=args.provider,
            algorithm=args.algorithm,
            language=args.language,
            test_success_only=bool(args.test_success_only),
            dedupe_slot=bool(args.dedupe_slot),
            limit=lim,
        )
        if not samples:
            print("未从历史库匹配到任何含 code 的记录。", file=sys.stderr)
            sys.exit(1)
        history_plain_n = len(samples)
        if args.wrap_as_fence:
            samples = [
                (label, lang, _wrap_as_fenced_llm_response(text, lang))
                for label, lang, text in samples
            ]
        if args.include_builtin_fence_samples:
            seen_labels = {s[0] for s in samples}
            for sample_id, lang, raw in BUILTIN_LLM_RAW_SAMPLES:
                if sample_id in seen_labels:
                    continue
                samples.append((sample_id, lang, raw))
        if args.export_corpus:
            exp_dir = args.export_corpus
            if not exp_dir.is_absolute():
                exp_dir = ROOT / exp_dir
            n_exp = _export_corpus(samples, exp_dir)
            print(f"[export] 已导出 {n_exp} 条到 {exp_dir}", file=sys.stderr)
        corpus_source = "code_history.code"
        history_meta = {
            "db": str(db_path.resolve()),
            "provider_filter": args.provider,
            "algorithm_filter": args.algorithm,
            "language_filter": args.language,
            "test_success_only": bool(args.test_success_only),
            "dedupe_slot": bool(args.dedupe_slot),
            "limit": lim,
            "history_plain_records": history_plain_n,
            "wrap_as_fence": bool(args.wrap_as_fence),
            "include_builtin_fence_samples": bool(args.include_builtin_fence_samples),
            "corpus_note": (
                "输入为落库源码"
                + ("经围栏包装模拟 LLM 回复" if args.wrap_as_fence else "（纯代码，Markdown 路径通常无围栏）")
                + (
                    f"；合并内置围栏样例 {len(BUILTIN_LLM_RAW_SAMPLES)} 条"
                    if args.include_builtin_fence_samples
                    else ""
                )
                + "。"
            ),
        }
        print(
            f"[from-history] 载入 {len(samples)} 条（db={db_path.name}，"
            f"test_success_only={args.test_success_only}，dedupe_slot={args.dedupe_slot}）",
            file=sys.stderr,
        )
        rows_detail, tot_md_t, tot_plain_t, tot_full_t = _run_metrics_on_samples(
            samples, args.language
        )
    elif args.glob:
        paths = _gather_files(args.glob)
        if not paths:
            print(f"未匹配到文件: {args.glob}", file=sys.stderr)
            sys.exit(1)
        for p in paths:
            lang = (args.language or _lang_from_path(p, None)).lower()
            raw = p.read_text(encoding="utf-8", errors="replace")
            m = _metrics_for_file(raw, lang)
            rows_detail.append({"file": str(p), "language": lang, **m})
            tot_md_t += m["t_md_ms"]
            tot_plain_t += m["t_plain_ms"]
            tot_full_t += m["t_full_ms"]
        corpus_source = "glob_files"
    else:
        print(
            "[aicrypto-helper] 未指定 --glob，使用内置样例 "
            f"（共 {len(BUILTIN_LLM_RAW_SAMPLES)} 条）。",
            file=sys.stderr,
        )
        for sample_id, lang, raw in BUILTIN_LLM_RAW_SAMPLES:
            eff_lang = (args.language or lang).lower()
            m = _metrics_for_file(raw, eff_lang)
            rows_detail.append({"file": sample_id, "language": eff_lang, **m})
            tot_md_t += m["t_md_ms"]
            tot_plain_t += m["t_plain_ms"]
            tot_full_t += m["t_full_ms"]
        corpus_source = "builtin_samples"

    n = len(rows_detail)

    def pct(num: float, den: int) -> float:
        return round(100.0 * num / den, 2) if den else 0.0

    sum_md = sum(1 for r in rows_detail if r["md_match"])
    sum_pl = sum(1 for r in rows_detail if r["plain_match"])
    n_nonempty = sum(1 for r in rows_detail if r["full_nonempty"])

    md_acc = pct(sum_md, n)
    pl_acc = pct(sum_pl, n)
    full_acc = pct(n_nonempty, n)
    sum_all_paths = sum(
        1
        for r in rows_detail
        if r["md_match"] and r["plain_match"] and r["full_nonempty"]
    )
    overall_acc = pct(sum_all_paths, n)

    avg_md = round(tot_md_t / n, 2) if n else 0.0
    avg_plain = round(tot_plain_t / n, 2) if n else 0.0
    avg_full = round(tot_full_t / n, 2) if n else 0.0
    # 「整体」行：三策略平均耗时的等权算术平均（非三行数值相加）
    overall_time_ms = round((avg_md + avg_plain + avg_full) / 3.0, 2)

    summary = {
        "corpus_source": corpus_source,
        "history": history_meta or None,
        "files_total": n,
        "samples_nonempty_extract": n_nonempty,
        "markdown_fence_agreement_pct": md_acc,
        "plain_text_path_agreement_pct": pl_acc,
        "full_nonempty_extract_pct": full_acc,
        "overall_strict_agreement_pct": overall_acc,
        "avg_time_ms": {
            "markdown_fence": avg_md,
            "plain_after_strip_fences": avg_plain,
            "full_extract_code": avg_full,
            "overall_equal_weight_mean_ms": overall_time_ms,
        },
        "note": (
            "启发式修复包含在 extract_code 全文路径中；"
            "「整体」行平均处理时间 = (Markdown + 纯文本 + 全文) 三策略各行均值的等权算术平均，"
            "非三行该列数值相加。"
        ),
    }

    payload = {"summary": summary, "detail": rows_detail}

    wrap_note = ""
    if history_meta.get("wrap_as_fence"):
        wrap_note = "历史库样本已包装为 Markdown 围栏回复；"
    elif corpus_source == "code_history.code":
        wrap_note = "历史库为纯代码输入（Markdown 路径通常无围栏）；"

    lines = [
        '<div align="center">',
        "",
        "**表 9　代码提取准确率与处理时间**",
        "",
        "</div>",
        "",
        f"*（`tab:extraction_accuracy`；样本 **{n}** 条，全文非空提取 **{n_nonempty}** 条；来源 **{corpus_source}**）*",
        "",
        "| 提取策略 | 准确率 (%) | 平均处理时间 (ms) |",
        "| :--- | :---: | :---: |",
        f"| Markdown 代码块 | {_fmt_pct_cell(md_acc)} | {_fmt_ms_cell(avg_md)} |",
        f"| 纯文本代码识别 | {_fmt_pct_cell(pl_acc)} | {_fmt_ms_cell(avg_plain)} |",
        f"| 启发式提取（全文多级 extract_code） | {_fmt_pct_cell(full_acc)} | {_fmt_ms_cell(avg_full)} |",
        f"| 整体提取成功率 | {_fmt_pct_cell(overall_acc)} | {_fmt_ms_cell(overall_time_ms)} |",
        "",
        "说明：前两行「准确率」为各路径与全文 `extract_code` 规范化结果的一致率；",
        "「启发式提取」行为非空提取成功占比；「整体提取成功率」为三路径均一致且全文非空提取的样本占比。",
        f"第四行「平均处理时间」= **(Markdown + 纯文本 + 全文) 三策略各行均值之和 ÷ 3**（等权平均，{wrap_note}非前三行数值相加）。",
        "",
        summary["note"],
        "",
    ]
    md_text = "\n".join(lines)
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)

    latex_snippet = (
        "\\begin{table}[H]\n"
        "  \\centering\n"
        "  \\footnotesize\n"
        "  \\caption{代码提取准确率与处理时间}\n"
        "  \\label{tab:extraction_accuracy}\n"
        "  \\setlength{\\tabcolsep}{6pt}\n"
        "  \\begin{tabular}{lcc}\n"
        "    \\toprule\n"
        "    提取策略 & 准确率 (\\%) & 平均处理时间 (ms) \\\\\n"
        "    \\midrule\n"
        f"    Markdown 代码块 & {_fmt_pct_cell(md_acc)} & {_fmt_ms_cell(avg_md)} \\\\\n"
        f"    纯文本代码识别 & {_fmt_pct_cell(pl_acc)} & {_fmt_ms_cell(avg_plain)} \\\\\n"
        f"    启发式提取（全文多级） & {_fmt_pct_cell(full_acc)} & {_fmt_ms_cell(avg_full)} \\\\\n"
        f"    整体提取成功率 & {_fmt_pct_cell(overall_acc)} & {_fmt_ms_cell(overall_time_ms)} \\\\\n"
        "    \\bottomrule\n"
        "  \\end{tabular}\n"
        "\\end{table}\n"
    )

    if args.output:
        outp = resolve_under_results(Path(args.output))
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(md_text, encoding="utf-8")
        print(f"已写入 Markdown: {outp}")
    else:
        print(md_text)

    if args.json_output:
        jp = resolve_under_results(Path(args.json_output))
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json_text, encoding="utf-8")
        print(f"已写入 JSON: {jp}")

    if args.latex_output:
        lp = resolve_under_results(Path(args.latex_output))
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(latex_snippet, encoding="utf-8")
        print(f"已写入 LaTeX: {lp}")


if __name__ == "__main__":
    main()
