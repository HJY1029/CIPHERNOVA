#!/usr/bin/env python3
"""
本地 Qwen **仅 DES**：蒸馏关 vs 开 对比（每格 ``web.server._batch_generate_single``）。

两阶段：**有蒸馏**在 ``local_batch_skip_enabled`` 时先历史复测，通过则沿用历史；**无蒸馏**强制重跑（不沿用历史）。

用法（仓库根目录）::

  python experiments/run_qwen_distill_des.py --dry-run
  python experiments/run_qwen_distill_des.py --invoke
  python experiments/run_qwen_distill_des.py --invoke --checkpoint distill_des_ckpt.json --resume

可与 ``run_qwen_distill_aes.py`` / ``rsa`` / ``sm4`` 并行。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.run_qwen_distillation_ablation import main_for_algorithm  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main_for_algorithm("DES"))
