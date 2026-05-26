#!/usr/bin/env bash
# SecCoder 论文设定下的「检索上下文 + 代码 LM」→ 写入 seccoder_des_out（12 文件）→ score
#
# 依赖：pip install openai；调用 OpenAI 兼容 API（默认 DeepSeek，与 selfrefine_des_deepseek 一致）。
# 不使用 PromptLoader / CryptoAgent / prompts/。
#
# 环境变量（均可选，有默认值）：
#   SECCODER_LM_BACKEND             openai | deepseek（默认 deepseek）
#   SECCODER_TASKS                  默认 $ROOT/experiments/rw_des_tasks.jsonl
#   SECCODER_DES_OUT                默认 $ROOT/experiments/seccoder_des_out
#   SECCODER_RETRIEVAL_MODE         none | random_file（默认 random_file）
#   SECCODER_SECURITY_SNIPPETS_DIR  默认 $ROOT/external/SecCoder_acl_security_snippets
#   SECCODER_LM_MODEL               可选，覆盖后端默认模型（deepseek 默认 deepseek-chat）
#   SECCODER_LM_BASE_URL            可选，覆盖后端默认 Base URL
#   SECCODER_API_KEY_ENV            可选，覆盖读 Key 的环境变量名
#   DEEPSEEK_API_KEY / OPENAI_API_KEY  至少设其一（与后端一致；胶水亦会回退另一变量名）
#   SECCODER_ARM                    默认 seccoder_glue_repro（写入 rw_seccoder.json）
#   SKIP_SCORE                      若设为 1，只跑胶水不写 JSON 汇总
#
# 说明：random_file 仅从目录抽样文件作「检索占位」，与附件 BM25/Instructor 不等价；
#       真对齐论文检索请自接 retriever_security.py 产出后再拼 prompt（见 §4.3）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

TASKS="${SECCODER_TASKS:-$ROOT/experiments/rw_des_tasks.jsonl}"
OUT="${SECCODER_DES_OUT:-$ROOT/experiments/seccoder_des_out}"
RETRIEVAL="${SECCODER_RETRIEVAL_MODE:-random_file}"
SNIPPETS="${SECCODER_SECURITY_SNIPPETS_DIR:-$ROOT/external/SecCoder_acl_security_snippets}"
BACKEND="${SECCODER_LM_BACKEND:-deepseek}"
ARM="${SECCODER_ARM:-seccoder_glue_repro}"

python experiments/related_work/rw_des_protocol_eval.py export -o "$TASKS"

_has_key=0
if [[ -n "${OPENAI_API_KEY:-}" ]]; then _has_key=1; fi
if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then _has_key=1; fi
if [[ -n "${SECCODER_API_KEY_ENV:-}" ]]; then
  eval "ALT=\"\${${SECCODER_API_KEY_ENV}:-}\""
  if [[ -n "${ALT:-}" ]]; then _has_key=1; fi
fi
if [[ "$_has_key" -eq 0 ]]; then
  echo "[seccoder_des_lm_pipeline] 未检测到 API Key：请 export DEEPSEEK_API_KEY=... 和/或 OPENAI_API_KEY（与 SECCODER_LM_BACKEND=$BACKEND 一致）" >&2
  exit 1
fi

GLUE_ARGS=(
  --tasks "$TASKS"
  --out-dir "$OUT"
  --retrieval-mode "$RETRIEVAL"
  --provider "$BACKEND"
)
[[ -n "${SECCODER_LM_MODEL:-}" ]] && GLUE_ARGS+=(--model "$SECCODER_LM_MODEL")
[[ -n "${SECCODER_LM_BASE_URL:-}" ]] && GLUE_ARGS+=(--base-url "$SECCODER_LM_BASE_URL")
[[ -n "${SECCODER_API_KEY_ENV:-}" ]] && GLUE_ARGS+=(--api-key-env "$SECCODER_API_KEY_ENV")

if [[ "$RETRIEVAL" == "random_file" ]]; then
  if [[ ! -d "$SNIPPETS" ]]; then
    echo "[seccoder_des_lm_pipeline] SECCODER_RETRIEVAL_MODE=random_file 需要已存在的目录:" >&2
    echo "  $SNIPPETS" >&2
    echo "  请先 mkdir 并放入若干安全相关源码，或 export SECCODER_SECURITY_SNIPPETS_DIR=...，或改用 SECCODER_RETRIEVAL_MODE=none" >&2
    exit 1
  fi
  GLUE_ARGS+=(--security-snippets-dir "$SNIPPETS")
fi

echo "[seccoder_des_lm_pipeline] LM 后端=$BACKEND，生成 12 个文件 → $OUT" >&2
python experiments/related_work/seccoder_des_glue_generate.py "${GLUE_ARGS[@]}"

if [[ "${SKIP_SCORE:-0}" == "1" ]]; then
  echo "[seccoder_des_lm_pipeline] SKIP_SCORE=1，已跳过 score。" >&2
  exit 0
fi

export SECCODER_DES_OUT="$OUT"
export SECCODER_ARM="$ARM"
bash experiments/related_work/run_rw_seccoder_des.sh
