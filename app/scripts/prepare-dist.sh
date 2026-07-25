#!/usr/bin/env bash
# 打包前置：把 uv 单二进制下载到 app/src-tauri/resources/bin/uv（随包带走，首启引导用它建 Python 运行时）。
# 用法：bash app/scripts/prepare-dist.sh   （npm run tauri build 之前跑一次即可，已存在则跳过）
set -euo pipefail

DEST_DIR="$(cd "$(dirname "$0")/../src-tauri" && pwd)/resources/bin"
mkdir -p "$DEST_DIR"
UV_BIN="$DEST_DIR/uv"
if [ -x "$UV_BIN" ]; then
  echo "uv 已就绪：$UV_BIN"
  exit 0
fi

ARCH="$(uname -m)"
case "$ARCH" in
  arm64) TRIPLE="aarch64-apple-darwin" ;;
  x86_64) TRIPLE="x86_64-apple-darwin" ;;
  *) echo "不支持的架构：$ARCH" >&2; exit 1 ;;
esac
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/astral-sh/uv/releases/latest/download/uv-$TRIPLE.tar.gz"
echo "下载 $URL"
curl -fSL "$URL" -o "$TMP/uv.tar.gz"
tar -xzf "$TMP/uv.tar.gz" -C "$TMP"
UV_EXTRACTED="$(find "$TMP" -name uv -type f -perm +111 | head -1)"
[ -n "$UV_EXTRACTED" ] || { echo "解压后找不到 uv 二进制" >&2; exit 1; }
install -m 755 "$UV_EXTRACTED" "$UV_BIN"
UV_VER="$("$UV_BIN" --version)"
echo "uv 就绪：${UV_BIN}（${UV_VER}）"
