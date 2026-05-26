#!/usr/bin/env bash
# DES 12 格 · Self-Refine + DeepSeek 一条龙：export → 生成 → score
# 需环境变量：DEEPSEEK_API_KEY 或 OPENAI_API_KEY（与 selfrefine_des_deepseek.py 一致）
# 可选：SELFREFINE_MAX_ITERS（默认 3）、SELFREFINE_OUT（默认 experiments/selfrefine_deepseek_out）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

OUT="${SELFREFINE_OUT:-$ROOT/experiments/selfrefine_deepseek_out}"
MAX_IT="${SELFREFINE_MAX_ITERS:-3}"
JSON_SUMMARY="$ROOT/experiments/rw_selfrefine_deepseek.json"

if [[ -z "${DEEPSEEK_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "[run_rw_selfrefine_des] 请设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY" >&2
  exit 1
fi

python experiments/related_work/run_rw_baseline_pipeline.py \
  --out-dir "$OUT" \
  --arm selfrefine_deepseek \
  --json-out "$JSON_SUMMARY" \
  -- \
  python experiments/related_work/selfrefine_des_deepseek.py \
    --out-dir "$OUT" \
    --max-iterations "$MAX_IT"

echo "[run_rw_selfrefine_des] 完成。汇总表：python experiments/related_work/rw_aggregate_rates.py --preset related-work -o experiments/rw_rates_table.md"
