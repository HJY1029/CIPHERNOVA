#!/usr/bin/env bash
# 克隆相关工作官方仓库至仓库根目录 external/（可选）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXT="$ROOT/external"
mkdir -p "$EXT"
cd "$EXT"

clone_skip() {
  local dir="$1"
  local url="$2"
  if [[ -d "$dir/.git" ]] || [[ -d "$dir" ]]; then
    echo "[skip] $dir 已存在"
  else
    echo "[clone] $url -> $dir"
    git clone --depth 1 "$url" "$dir"
  fi
}

clone_skip "sven" "https://github.com/eth-sri/sven.git"
clone_skip "AgentCoder" "https://github.com/huangd1999/AgentCoder.git"
clone_skip "self-refine" "https://github.com/madaan/self-refine.git"

echo ""
echo "完成。SecCoder 请参考 ACL Anthology 论文页下载artifact或作者提供的压缩包，解压到: $EXT/SecCoder"
echo "路径: $EXT"
