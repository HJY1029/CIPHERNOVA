#!/usr/bin/env python3
"""
本地 Qwen（``qwen_coder_local``）仅跑 **RSA**（三语言，无 mode）网格。

与 Web 批量页同源：``web.server._batch_generate_single``。

用法（仓库根目录）::

  python scripts/run_qwen_batch_rsa.py --dry-run
  python scripts/run_qwen_batch_rsa.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.qwen_batch_common import main_for_algorithm  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main_for_algorithm("RSA"))
