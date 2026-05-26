#!/usr/bin/env bash
# DES 12 格 · SVEN 一条龙：export → YAML → human_eval_gen → 抽取 → score
# 用法：在仓库根执行  bash experiments/related_work/run_rw_sven_des.sh
# 可选环境变量：
#   SVEN_MODEL_DIR   默认 350m（短名，映射 HuggingFace 上的 Salesforce/codegen-350M-multi）；
#                    也可设为**本机已下载的模型目录绝对路径**（见下文），则不再从网络拉取。
#   SVEN_OUTPUT_NAME 默认 sven-des-lm-350m
#   SKIP_PIP         若设为 1，跳过 pip install（已装好环境时）
#
# 无法直连 huggingface.co 时（国内/离线常见）二选一：
#   A) 使用镜像（在跑本脚本**之前**于同一 shell 执行）：
#        export HF_ENDPOINT=https://hf-mirror.com
#   B) 在有网机器上先下载到本地，再设 SVEN_MODEL_DIR 为该目录，例如：
#        huggingface-cli download Salesforce/codegen-350M-multi --local-dir /path/to/codegen-350m
#        export SVEN_MODEL_DIR=/path/to/codegen-350m

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

SVEN_MODEL_DIR="${SVEN_MODEL_DIR:-350m}"
SVEN_OUTPUT_NAME="${SVEN_OUTPUT_NAME:-sven-des-lm-350m}"

# human_eval_gen 里 short name → HF id；绝对路径则直接使用
if [[ "$SVEN_MODEL_DIR" == /* ]] || [[ "$SVEN_MODEL_DIR" == [A-Za-z]:/* ]]; then
  _ARM_BASE="$(basename "$SVEN_MODEL_DIR")"
else
  _ARM_BASE="$SVEN_MODEL_DIR"
fi
YAML_DIR="$ROOT/external/sven/data_eval/des_rw"
GEN_OUT_REL="external/sven/experiments/des_rw/${SVEN_OUTPUT_NAME}"
EXTRACT_OUT="$ROOT/experiments/sven_des_outputs"
TASKS="$ROOT/experiments/rw_des_tasks.jsonl"
JSON_SUMMARY="$ROOT/experiments/rw_sven.json"

python experiments/related_work/rw_des_protocol_eval.py export -o "$TASKS"
python experiments/related_work/sven_des_problem_yamls.py -o "$YAML_DIR"

if [[ "${SKIP_PIP:-0}" != "1" ]]; then
  ( cd "$ROOT/external/sven" && pip install torch && pip install -r requirements_des_rw.txt && pip install -e . )
else
  echo "[run_rw_sven_des] SKIP_PIP=1，跳过依赖安装"
fi

if [[ -n "${HF_ENDPOINT:-}" ]]; then
  echo "[run_rw_sven_des] 使用 HF_ENDPOINT=${HF_ENDPOINT}（镜像）"
fi

( cd "$ROOT/external/sven/scripts" && python human_eval_gen.py \
  --eval_type des_rw \
  --data_dir ../data_eval \
  --output_dir ../experiments \
  --model_type lm \
  --model_dir "$SVEN_MODEL_DIR" \
  --output_name "$SVEN_OUTPUT_NAME" \
  --num_samples 1 \
  --num_samples_per_gen 1 )

python experiments/related_work/sven_des_extract_completions.py \
  --tasks "$TASKS" \
  --yaml-dir "$ROOT/$GEN_OUT_REL" \
  --out-dir "$EXTRACT_OUT"

ARM="sven_lm_${_ARM_BASE}"
python experiments/related_work/rw_des_protocol_eval.py score \
  --inputs "$EXTRACT_OUT" \
  --arm "$ARM" \
  --no-canonical-whole-file \
  -o "$JSON_SUMMARY"

echo "[run_rw_sven_des] 完成。汇总表：python experiments/related_work/rw_aggregate_rates.py --preset related-work -o experiments/rw_rates_table.md"
