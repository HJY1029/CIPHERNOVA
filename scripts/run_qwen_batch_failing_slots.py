#!/usr/bin/env python3
"""
Qwen 本地批量：跑 **未过槽** + **仅 llm_performance 通过但 code_history 无 qwen 落库** 的槽
（``QWEN_FAILING_SLOT_KEYS`` + ``QWEN_FORCE_REGEN_NO_DB_KEYS``，见 ``qwen_batch_common``）。

不跑其它已通过且 DB 有 qwen 成功记录的槽。无 DB 的 7 格**不做预筛跳过**，必定调 LLM 落库。
**默认跳过教师蒸馏**（``_skip_distillation``，难槽易串题；依赖算法 YAML + 测试反馈）。

用法（仓库根目录）::

  python scripts/run_qwen_batch_failing_slots.py --dry-run
  python scripts/run_qwen_batch_failing_slots.py
  python scripts/run_qwen_batch_failing_slots.py --force-all   # 强制全部未过槽调 LLM
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.qwen_batch_common import main_failing_slots  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main_failing_slots())
