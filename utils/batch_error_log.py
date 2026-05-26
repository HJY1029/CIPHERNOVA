"""批量生成失败时写入可读文本日志，便于根据错误改进 prompt。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TextIO


def write_batch_error_log_header(
    fp: TextIO,
    *,
    title: str,
    extra_lines: Optional[list[str]] = None,
) -> None:
    fp.write(f"# {title}\n")
    fp.write(f"# started_utc: {datetime.now(timezone.utc).isoformat()}\n")
    if extra_lines:
        for line in extra_lines:
            fp.write(f"# {line}\n")
    fp.write("# 以下仅包含 success=False 的条目。\n\n")


def append_batch_failure(
    fp: TextIO,
    *,
    provider: str,
    config: Dict[str, Any],
    result: Dict[str, Any],
    max_detail: int = 16000,
    max_generated_code: int = 120000,
) -> None:
    """若 result['success'] 为假，追加一块可读记录（与 Web _batch_generate_single / run_one 返回字段兼容）。"""
    if result.get("success"):
        return
    err = result.get("error")
    gen_ok = result.get("generation_ok")
    vs = result.get("vector_status")
    vd = result.get("vector_detail") or ""
    if len(vd) > max_detail:
        vd = vd[:max_detail] + f"\n…（截断，原长 {len(result.get('vector_detail') or '')}）"
    case_id = result.get("case_id") or ""
    ms = result.get("total_ms")
    refs = result.get("prompt_refs")
    gcode = result.get("generated_code")
    gfile = result.get("generated_filename")
    fp.write("=" * 80 + "\n")
    fp.write(f"time_utc: {datetime.now(timezone.utc).isoformat()}\n")
    fp.write(f"provider: {provider}\n")
    if case_id:
        fp.write(f"case_id: {case_id}\n")
    fp.write(f"config: {json.dumps(config, ensure_ascii=False)}\n")
    fp.write(f"success: False\n")
    if gen_ok is not None:
        fp.write(f"generation_ok: {gen_ok}\n")
    if vs is not None:
        fp.write(f"vector_status: {vs}\n")
    if vd:
        fp.write(f"vector_detail:\n{vd}\n")
    if err is not None:
        fp.write(f"error:\n{err}\n")
    if ms is not None:
        fp.write(f"total_ms: {ms}\n")
    if refs:
        fp.write(f"prompt_refs: {json.dumps(refs, ensure_ascii=False)}\n")
    if gfile:
        fp.write(f"generated_filename: {gfile}\n")
    if isinstance(gcode, str) and gcode.strip():
        raw = gcode
        if len(raw) > max_generated_code:
            raw = raw[:max_generated_code] + f"\n…（generated_code 截断，原长 {len(gcode)}）"
        fp.write("generated_code:\n```\n")
        fp.write(raw)
        fp.write("\n```\n")
    fp.write("-" * 80 + "\n\n")
    fp.flush()


def default_matrix_errors_path(root: Path, providers: Optional[list] = None) -> Path:
    """批量矩阵默认失败日志路径（``experiments/results/``）。若仅跑 ``qwen_coder_local``，使用 ``qwen_batch_generation_errors_*.txt`` 前缀。"""
    from experiments.experiment_outputs import experiments_results_dir

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = experiments_results_dir()
    prefix = "batch_generation_errors"
    if providers is not None and len(providers) == 1 and providers[0] == "qwen_coder_local":
        prefix = "qwen_batch_generation_errors"
    return d / f"{prefix}_{ts}.txt"


def default_qwen_errors_path(root: Path) -> Path:
    from experiments.experiment_outputs import experiments_results_dir

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return experiments_results_dir() / f"qwen_batch_errors_{ts}.txt"
