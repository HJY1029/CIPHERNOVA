#!/usr/bin/env bash
# 从各基线 score 生成的 JSON 写出 GSR/VPR/FTPR Markdown 表（仅包含已存在的 rw_*.json）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"
OUT="${1:-$ROOT/experiments/rw_rates_table.md}"
FMT="${2:-markdown}"
python experiments/related_work/rw_aggregate_rates.py --preset related-work -o "$OUT" --format "$FMT"
echo "[run_rw_aggregate_table] → $OUT"
