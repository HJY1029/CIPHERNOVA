#!/usr/bin/env python3
"""
将 SVEN ``human_eval_gen.py`` 输出目录中的 Problem YAML 转为 ``rw_des_protocol_eval.py score`` 所需的源码文件。

默认写入 ``prompt + completion``（与 LM 续写一致）；若模型已输出完整文件，可加 ``--completion-only``。

用法：
  python experiments/related_work/sven_des_extract_completions.py \\
    --tasks experiments/rw_des_tasks.jsonl \\
    --yaml-dir external/sven/experiments/des_rw/run-name \\
    --out-dir experiments/sven_des_score_inputs
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _problem_name(row: Dict[str, Any]) -> str:
    mode = (row.get("mode") or "ecb").lower()
    lang = (row.get("language") or "python").lower()
    stem = f"des_{mode}_{lang}"
    return stem.replace(".", "_")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="从 SVEN 生成的 YAML 抽取源码到 des_<mode>.<ext>"
    )
    ap.add_argument(
        "--tasks",
        type=Path,
        default=ROOT / "experiments" / "rw_des_tasks.jsonl",
        help="export 产出的 JSONL（用于 expected_filename）",
    )
    ap.add_argument(
        "--yaml-dir",
        type=Path,
        required=True,
        help="human_eval_gen 写入的问题目录（含 *.yaml，排除 *.results.yaml）",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="输出目录（将写入 12 个 des_*.py|c|cpp）",
    )
    ap.add_argument(
        "--completion-index",
        type=int,
        default=-1,
        help="使用 completions 列表中的第几条（默认 -1 为最后一条）",
    )
    ap.add_argument(
        "--completion-only",
        action="store_true",
        help="仅写 completion，不拼接 prompt",
    )
    args = ap.parse_args()

    if not args.yaml_dir.is_dir():
        print(f"[extract] 目录不存在: {args.yaml_dir}", file=sys.stderr)
        return 2

    import yaml

    args.out_dir.mkdir(parents=True, exist_ok=True)
    tasks = _load_tasks(args.tasks)
    written = 0

    for row in tasks:
        name = _problem_name(row)
        ypath = args.yaml_dir / f"{name}.yaml"
        if not ypath.is_file():
            print(f"[extract] 跳过（无 YAML）: {ypath}", file=sys.stderr)
            continue

        with open(ypath, encoding="utf-8") as f:
            prob = yaml.safe_load(f)

        comps = prob.get("completions") or []
        if not comps:
            print(f"[extract] 跳过（无 completions）: {ypath}", file=sys.stderr)
            continue

        idx = args.completion_index
        try:
            completion = comps[idx]
        except IndexError:
            print(f"[extract] completions 索引越界 {idx}: {ypath}", file=sys.stderr)
            return 3

        prompt = (prob.get("prompt") or "") if not args.completion_only else ""
        code = completion if args.completion_only else (prompt + completion)

        out_name = row.get("expected_filename")
        if not out_name:
            print(f"[extract] 行缺少 expected_filename: {row.get('task_id')}", file=sys.stderr)
            return 4
        dest = args.out_dir / out_name
        dest.write_text(code, encoding="utf-8")
        print(f"[extract] {ypath.name} -> {dest}", file=sys.stderr)
        written += 1

    print(
        f"[extract] 共写入 {written}/{len(tasks)} 个文件 → {args.out_dir.resolve()}",
        file=sys.stderr,
    )
    print(
        "下一步: python experiments/related_work/rw_des_protocol_eval.py score "
        f"--inputs {args.out_dir} --arm <你的 arm 标签> --no-canonical-whole-file",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
