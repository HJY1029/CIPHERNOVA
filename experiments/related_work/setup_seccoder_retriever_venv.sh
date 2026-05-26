#!/usr/bin/env bash
# 在 SecCoder ACL software 解压目录旁创建独立 venv，避免污染 conda base。
# 用法（在仓库根目录）:  bash experiments/related_work/setup_seccoder_retriever_venv.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CODE="$ROOT/external/SecCoder_acl_software/code"
if [[ ! -f "$CODE/retriever_security.py" ]]; then
  echo "[error] 未找到 $CODE/retriever_security.py"
  echo "请先: bash experiments/related_work/fetch_seccoder_acl.sh"
  echo "再:   unzip -q -o external/2024.emnlp-main.806.software.zip -d external/SecCoder_acl_software"
  exit 1
fi

VENV="$ROOT/external/SecCoder_acl_software/.venv"
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
  echo "[ok] 已创建 $VENV"
else
  echo "[info] 已存在 $VENV，跳过 python -m venv"
fi

# shellcheck source=/dev/null
source "$VENV/bin/activate"
python -m pip install -U pip wheel
python -m pip install torch transformers rank_bm25 sentencepiece scikit-learn

echo ""
echo "下一步（每次新开终端要先激活 venv）:"
echo "  source $VENV/bin/activate"
echo "  cd $CODE"
echo ""
echo "retriever_security.py：--score_method random / None 仅需当前依赖（本仓库已改为惰性 import Instructor）。"
echo "若用 --score_method INSTRUCTOR，另需: pip install InstructorEmbedding"
echo ""
echo "若无法访问 HuggingFace（SSL/网络），二选一:"
echo "  A) random 模式:     python retriever_security.py --score_method random --security_code_dir ... --evaluation_dir ... --output_dir ..."
echo "  B) 离线模型目录:    --tokenizer_dir /你本机已下载的/codebert 目录（含 config.json tokenizer.json）"
echo "  C) 镜像（视环境）:   export HF_ENDPOINT=https://hf-mirror.com"
