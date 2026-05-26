#!/usr/bin/env python3
"""
本地 Qwen（``qwen_coder_local``）仅跑 **DES** 全模式×语言网格。

与 Web 批量页同源：``web.server._batch_generate_single``（``enable_validation=False``、
``validate=False``、``max_retries=3``、``local_batch_skip_*`` 历史复测跳过）。

用法（仓库根目录）::

  python scripts/run_qwen_batch_des.py --dry-run
  python scripts/run_qwen_batch_des.py
  python scripts/run_qwen_batch_des.py --only-pending
  python scripts/run_qwen_batch_des.py --limit 4 --offset 4

可与 ``run_qwen_batch_aes.py`` / ``rsa`` / ``sm4`` 在不同终端并行。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.qwen_batch_common import main_for_algorithm  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main_for_algorithm("DES"))
