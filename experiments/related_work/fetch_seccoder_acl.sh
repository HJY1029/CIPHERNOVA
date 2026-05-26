#!/usr/bin/env bash
# 从 ACL Anthology 下载 SecCoder（EMNLP 2024）官方附件：推理代码 software.zip、数据 data.zip
# 用法：在仓库根目录执行  bash experiments/related_work/fetch_seccoder_acl.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXT="$ROOT/external"
mkdir -p "$EXT"
cd "$EXT"

SOFTWARE_URL="https://aclanthology.org/attachments/2024.emnlp-main.806.software.zip"
DATA_URL="https://aclanthology.org/attachments/2024.emnlp-main.806.data.zip"

fetch() {
  local url="$1" name="$2"
  if [[ -f "$name" ]]; then
    echo "[skip] 已存在: $EXT/$name"
  else
    echo "[get] $url"
    curl -L -o "$name" "$url" || wget -O "$name" "$url"
  fi
}

fetch "$SOFTWARE_URL" "2024.emnlp-main.806.software.zip"
fetch "$DATA_URL" "2024.emnlp-main.806.data.zip"

echo ""
echo "下一步：解压 software.zip；附件仅含 code/retriever_*.py（见根目录 相关工作对比实验.md §4）。DES 任务：rw_des_protocol_eval export。"
echo "  unzip -q -o 2024.emnlp-main.806.software.zip -d SecCoder_acl_software"
echo "路径: $EXT"
