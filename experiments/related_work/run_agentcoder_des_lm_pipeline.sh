#!/usr/bin/env bash
# AgentCoder「仅 Programmer」风格：OpenAI 兼容 LM → agentcoder_des_out → score
# 与 run_seccoder_des_lm_pipeline.sh 对称；须 openai>=1.0。
#
#   SECCODER_LM_BACKEND → 本脚本用 AGENTCODER_LM_BACKEND（默认 deepseek）
#   其余变量同 run_seccoder_des_lm_pipeline.sh 头注释（SECCODER_* → AGENTCODER_*）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

TASKS="${AGENTCODER_TASKS:-$ROOT/experiments/rw_des_tasks.jsonl}"
OUT="${AGENTCODER_DES_OUT:-$ROOT/experiments/agentcoder_des_out}"
RETRIEVAL="${AGENTCODER_RETRIEVAL_MODE:-none}"
SNIPPETS="${AGENTCODER_SECURITY_SNIPPETS_DIR:-$ROOT/external/SecCoder_acl_security_snippets}"
BACKEND="${AGENTCODER_LM_BACKEND:-deepseek}"
ARM="${AGENTCODER_ARM:-agentcoder_des}"

python experiments/related_work/rw_des_protocol_eval.py export -o "$TASKS"

_has_key=0
[[ -n "${OPENAI_API_KEY:-}" ]] && _has_key=1
[[ -n "${DEEPSEEK_API_KEY:-}" ]] && _has_key=1
if [[ -n "${AGENTCODER_API_KEY_ENV:-}" ]]; then
  eval "ALT=\"\${${AGENTCODER_API_KEY_ENV}:-}\""
  [[ -n "${ALT:-}" ]] && _has_key=1
fi
if [[ "$_has_key" -eq 0 ]]; then
  echo "[agentcoder_des_lm_pipeline] 请 export DEEPSEEK_API_KEY 或 OPENAI_API_KEY（与 AGENTCODER_LM_BACKEND=$BACKEND 一致）" >&2
  exit 1
fi

GLUE_ARGS=(
  --tasks "$TASKS"
  --out-dir "$OUT"
  --retrieval-mode "$RETRIEVAL"
  --provider "$BACKEND"
)
[[ -n "${AGENTCODER_LM_MODEL:-}" ]] && GLUE_ARGS+=(--model "$AGENTCODER_LM_MODEL")
[[ -n "${AGENTCODER_LM_BASE_URL:-}" ]] && GLUE_ARGS+=(--base-url "$AGENTCODER_LM_BASE_URL")
[[ -n "${AGENTCODER_API_KEY_ENV:-}" ]] && GLUE_ARGS+=(--api-key-env "$AGENTCODER_API_KEY_ENV")
[[ "${AGENTCODER_NO_FEW_SHOT:-0}" == "1" ]] && GLUE_ARGS+=(--no-few-shot)
if [[ -n "${AGENTCODER_FEW_SHOT_PROMPT:-}" ]]; then
  GLUE_ARGS+=(--agentcoder-prompt "$AGENTCODER_FEW_SHOT_PROMPT")
fi

if [[ "$RETRIEVAL" == "random_file" ]]; then
  if [[ ! -d "$SNIPPETS" ]]; then
    echo "[agentcoder_des_lm_pipeline] random_file 需要目录: $SNIPPETS" >&2
    exit 1
  fi
  GLUE_ARGS+=(--security-snippets-dir "$SNIPPETS")
fi

echo "[agentcoder_des_lm_pipeline] LM 后端=$BACKEND → $OUT" >&2
python experiments/related_work/agentcoder_des_programmer_glue.py "${GLUE_ARGS[@]}"

if [[ "${SKIP_SCORE:-0}" == "1" ]]; then
  echo "[agentcoder_des_lm_pipeline] SKIP_SCORE=1，已跳过 score。" >&2
  exit 0
fi

export AGENTCODER_DES_OUT="$OUT"
export AGENTCODER_ARM="$ARM"
bash experiments/related_work/run_rw_agentcoder_des.sh
