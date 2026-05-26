"""
实验产物统一目录：仓库内约定写入 ``experiments/results/``（相对路径默认落此目录，绝对路径不变）。
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_RESULTS_DIR = _REPO_ROOT / "experiments" / "results"


def experiments_results_dir() -> Path:
    EXPERIMENTS_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return EXPERIMENTS_RESULTS_DIR


def resolve_under_results(path: Path) -> Path:
    """相对路径 → ``experiments/results/<path>``；已是绝对路径则原样返回。"""
    if path.is_absolute():
        return path
    experiments_results_dir()
    return EXPERIMENTS_RESULTS_DIR / path


def repo_root() -> Path:
    return _REPO_ROOT
