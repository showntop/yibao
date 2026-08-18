#!/usr/bin/env bash
# 打包前置：把 uv 单二进制下载到 app/src-tauri/resources/bin/uv（随包带走，首启引导用它建 Python 运行时）；
# 并硬检查 coding:studio 面板产物（plugins/*/panel/dist/ 不进 git，干净机器必须现构建，缺失则面板 iframe 全 404，
# chat.html 已退役、无兜底）。
# 用法：bash app/scripts/prepare-dist.sh   （npm run tauri build 之前跑一次即可，已存在则跳过）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST_DIR="$(cd "$(dirname "$0")/../src-tauri" && pwd)/resources/bin"
mkdir -p "$DEST_DIR"
UV_BIN="$DEST_DIR/uv"
if [ -x "$UV_BIN" ]; then
  echo "uv 已就绪：$UV_BIN"
else
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
fi

# coding:studio 面板产物硬检查：缺入口则现构建；构建链不可用（node/pnpm 缺）立即报错误出，不放行打包
PANEL_ENTRY="$REPO_ROOT/plugins/coding/panel/dist/index.html"
if [ ! -f "$PANEL_ENTRY" ]; then
  echo "panel dist 缺失，构建 plugins/coding/panel …"
  command -v node >/dev/null 2>&1 || { echo "错误：构建 panel 需要 node（node scripts/panel-build/build.mjs coding），请先安装 Node.js" >&2; exit 1; }
  if [ ! -d "$REPO_ROOT/scripts/panel-build/node_modules" ] || [ ! -d "$REPO_ROOT/plugins/coding/panel/node_modules" ]; then
    command -v pnpm >/dev/null 2>&1 || { echo "错误：缺 panel 构建依赖且 pnpm 不可用；请安装 pnpm 后在 scripts/panel-build 与 plugins/coding/panel 各跑一次 pnpm install" >&2; exit 1; }
    (cd "$REPO_ROOT/scripts/panel-build" && pnpm install)
    (cd "$REPO_ROOT/plugins/coding/panel" && pnpm install)
  fi
  (cd "$REPO_ROOT" && node scripts/panel-build/build.mjs coding)
  [ -f "$PANEL_ENTRY" ] || { echo "错误：panel 构建跑完仍缺产物 $PANEL_ENTRY" >&2; exit 1; }
fi
echo "panel 就绪：$PANEL_ENTRY"
