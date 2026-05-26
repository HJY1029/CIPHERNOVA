#!/usr/bin/env bash
# DES 12 格 · AgentCoder 口径：export → 检查 12 文件 → score
# 上游默认 HumanEval，须自行改流水线读取 rw_des_tasks.jsonl 并输出约定文件名。
# 环境变量：
#   AGENTCODER_DES_OUT  默认 ROOT/experiments/agentcoder_des_out
#   AGENTCODER_ARM      默认 agentcoder_des

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

OUT="${AGENTCODER_DES_OUT:-$ROOT/experiments/agentcoder_des_out}"
ARM="${AGENTCODER_ARM:-agentcoder_des}"
JSON_SUMMARY="$ROOT/experiments/rw_agentcoder.json"

TASKS="$ROOT/experiments/rw_des_tasks.jsonl"
python experiments/related_work/rw_des_protocol_eval.py export -o "$TASKS"

mkdir -p "$OUT"
need=(
  des_ecb.py des_cbc.py des_cfb.py des_ofb.py
  des_ecb.c des_cbc.c des_cfb.c des_ofb.c
  des_ecb.cpp des_cbc.cpp des_cfb.cpp des_ofb.cpp
)
found=0
for f in "${need[@]}"; do
  [[ -f "$OUT/$f" ]] && found=$((found + 1)) || true
done
if [[ "$found" -lt 12 ]]; then
  echo "[run_rw_agentcoder_des] 仅在 $OUT 找到 $found/12 个约定文件。" >&2
  echo "  一键 Programmer 胶水（与 SecCoder 胶水同风格）: bash experiments/related_work/run_agentcoder_des_lm_pipeline.sh" >&2
  echo "  或: python experiments/related_work/agentcoder_des_programmer_glue.py --out-dir \"$OUT\" …（见 相关工作对比实验.md §2.4 / §5）" >&2
  exit 1
fi

python experiments/related_work/rw_des_protocol_eval.py score \
  --inputs "$OUT" \
  --arm "$ARM" \
  --no-canonical-whole-file \
  -o "$JSON_SUMMARY"

echo "[run_rw_agentcoder_des] 完成。汇总表：python experiments/related_work/rw_aggregate_rates.py --preset related-work -o experiments/rw_rates_table.md"
