#!/usr/bin/env python3
"""本地 Qwen **仅 AES**：蒸馏关 vs 开（有蒸馏沿用历史，无蒸馏强制重跑）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.run_qwen_distillation_ablation import main_for_algorithm  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main_for_algorithm("AES"))
