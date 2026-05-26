#!/usr/bin/env bash
# DES 12 格 · SecCoder 口径：export → 检查 12 文件 → score
# 官方附件无 DES 一键生成，须在你复现的 SecCoder+LM 流程中写入目录后再跑本脚本。
# 环境变量：
#   SECCODER_DES_OUT  默认 ROOT/experiments/seccoder_des_out
#   SECCODER_ARM      默认 seccoder_repro

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

OUT="${SECCODER_DES_OUT:-$ROOT/experiments/seccoder_des_out}"
ARM="${SECCODER_ARM:-seccoder_repro}"
JSON_SUMMARY="$ROOT/experiments/rw_seccoder.json"

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
  echo "[run_rw_seccoder_des] 仅在 $OUT 找到 $found/12 个约定文件。" >&2
  echo "  请按 experiments/rw_des_tasks.jsonl 每行 expected_filename，在 SecCoder 复现流程中生成并保存到该目录，再重新运行本脚本。" >&2
  echo "  一键检索+LM+score：bash experiments/related_work/run_seccoder_des_lm_pipeline.sh（见 相关工作对比实验.md §2.3）" >&2
  echo "  或手动：python experiments/related_work/seccoder_des_glue_generate.py --out-dir \"$OUT\" …" >&2
  echo "  检索示例见 experiments/related_work/fetch_seccoder_acl.sh 与 相关工作对比实验.md §4。" >&2
  exit 1
fi

python experiments/related_work/rw_des_protocol_eval.py score \
  --inputs "$OUT" \
  --arm "$ARM" \
  --no-canonical-whole-file \
  -o "$JSON_SUMMARY"

echo "[run_rw_seccoder_des] 完成。汇总表：python experiments/related_work/rw_aggregate_rates.py --preset related-work -o experiments/rw_rates_table.md"
