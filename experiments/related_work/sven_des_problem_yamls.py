#!/usr/bin/env python3
"""
为 SVEN ``human_eval_gen.py`` 生成 DES 12 格的 **官方题目 YAML**（与 HumanEval 式 Problem 同 schema）。

**与「本文分层提示词」无关**：
  - **不**使用本仓库 ``prompts/``、``PromptLoader``、``build_prompt``、``CryptoAgent``。
  - 输出文件里的键名 ``prompt`` **不是**我们起的名字，而是 SVEN 上游
    ``sven/human_eval/problem_yaml.py`` 里 ``Problem.prompt`` 的**固定字段**；
    ``human_eval_gen.py`` 只认这个键，改成别的键 SVEN 会直接读失败。
  - 该字段内容仅为**极短中性续写前缀**（算法/模式/语言/I/O 一句约定），与论文「可组合提示工程」不是同一套东西。
  - 最终 GSR/VPR/FTPR 仍以本仓库 ``rw_des_protocol_eval.py score`` + ``test_data.yaml`` 为准。

用法：
  python experiments/related_work/sven_des_problem_yamls.py \\
    -o external/sven/data_eval/des_rw \\
    --tasks experiments/rw_des_tasks.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sven_yaml_prompt_field(row: Dict[str, Any]) -> str:
    """供写入 SVEN Problem YAML 的 ``prompt`` 键：续写前缀，非 prompts/ 模板。"""
    lang = (row.get("language") or "python").lower()
    mode = row.get("mode") or "ECB"
    iv_note = "IV from TEST_IV (hex)." if mode.upper() != "ECB" else "(IV not used for ECB)."
    if lang == "python":
        iv_extra = " and IV" if mode.upper() != "ECB" else ""
        iv_env = ", TEST_IV" if mode.upper() != "ECB" else ""
        return (
            f'"""Task: DES-{mode} encrypt in Python. '
            f"Read plaintext/key{iv_extra} from environment variables "
            f"TEST_PLAINTEXT, TEST_KEY{iv_env} (hex strings). Print ciphertext as "
            f"lowercase hex. {iv_note} Output only complete runnable code.\"\"\"\n\n"
        )
    if lang == "c":
        return (
            f"/* Task: DES-{mode} encrypt in C. Read TEST_PLAINTEXT, TEST_KEY"
            + (", TEST_IV" if mode.upper() != "ECB" else "")
            + " from env (hex). Print ciphertext hex. Complete the program below. */\n\n"
            "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n\nint main(void) {\n"
        )
    # cpp
    return (
        f"// Task: DES-{mode} encrypt in C++. Read TEST_PLAINTEXT, TEST_KEY"
        + (", TEST_IV" if mode.upper() != "ECB" else "")
        + " from env (hex). Print ciphertext hex.\n"
        "#include <iostream>\n#include <string>\n\nint main() {\n"
    )


def _tests_placeholder(lang: str) -> str:
    """占位测试串；HumanEval 流水线需要字段；最终 FTPR 以主仓库 CodeTester 为准。"""
    if lang == "python":
        return "def check():\n    assert True\ncheck()\n"
    return "int main(){return 0;}\n"


def _stop_tokens(lang: str) -> list:
    if lang == "python":
        return ["\ndef", "\n#", "\nif", "\nclass"]
    return ["\nint main", "\nvoid ", "\nclass "]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="生成 SVEN 官方 Problem YAML（键名 prompt 为上游 schema 要求，非本仓库 prompts/）"
    )
    ap.add_argument(
        "--tasks",
        type=Path,
        default=ROOT / "experiments" / "rw_des_tasks.jsonl",
        help="rw_des_protocol_eval.py export 产出的 JSONL",
    )
    ap.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="SVEN data_eval 下子目录，如 external/sven/data_eval/des_rw",
    )
    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(
        "[sven_des_problem_yamls] 写入的是 SVEN human_eval 题目 YAML；"
        "其中键 prompt 为官方 Problem 类固定字段（续写前缀），未使用本仓库 prompts/ 或 PromptLoader。",
        file=sys.stderr,
    )

    rows: list[Dict[str, Any]] = []
    with open(args.tasks, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    import yaml

    for row in rows:
        lang = (row.get("language") or "python").lower()
        mode = (row.get("mode") or "ecb").lower()
        stem = f"des_{mode}_{lang}"
        name = stem.replace(".", "_")
        prob = {
            "name": name,
            "language": {"python": "py", "c": "c", "cpp": "cpp"}.get(lang, "py"),
            # 键名必须为 prompt：见 external/sven/sven/human_eval/problem_yaml.py
            "prompt": _sven_yaml_prompt_field(row),
            "tests": _tests_placeholder(lang),
            "completions": [],
            "stop_tokens": _stop_tokens(lang),
        }
        out_path = args.output_dir / f"{name}.yaml"
        with open(out_path, "w", encoding="utf-8") as fo:
            yaml.safe_dump(
                prob,
                fo,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        print(f"[sven_des_problem_yamls] wrote {out_path}", file=sys.stderr)

    print(
        f"[sven_des_problem_yamls] 共 {len(rows)} 个 YAML → {args.output_dir.resolve()}",
        file=sys.stderr,
    )
    print(
        "下一步在 external/sven/scripts 运行 human_eval_gen.py，并指定 "
        "--eval_type des_rw --num_samples 1 --num_samples_per_gen 1（见根目录 相关工作对比实验.md §2）",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
