#!/bin/bash
# 安装「手动同步」方案：一个可双击的按钮 + 一个纯 Python 脚本（无 Node、无后台进程）
set -euo pipefail

BASE="$HOME/.wb-sync"
APP="$HOME/Applications/任务同步.app"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/sync"

say() { printf '%s\n' "$*"; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "本脚本仅支持 macOS。"
[ -x /usr/bin/python3 ] || die "缺少 /usr/bin/python3（macOS 自带组件，请先安装命令行工具）。"

mkdir -p "$BASE" "$HOME/Applications"
chmod 700 "$BASE"

install -m 755 "$SRC/sync.py" "$BASE/sync.py"
say "已安装同步脚本：$BASE/sync.py"

if [ ! -f "$BASE/config.json" ]; then
  [ -n "${WB_ENDPOINT:-}" ] && [ -n "${WB_INGEST_TOKEN:-}" ] \
    || die "缺少配置：请设置 WB_ENDPOINT / WB_PUBLISHABLE_KEY / WB_INGEST_TOKEN。"
  cat > "$BASE/config.json" <<JSON
{
  "endpoint": "${WB_ENDPOINT}",
  "publishableKey": "${WB_PUBLISHABLE_KEY}",
  "ingestToken": "${WB_INGEST_TOKEN}"
}
JSON
  chmod 600 "$BASE/config.json"
  say "已写入配置。"
else
  say "沿用已有配置：$BASE/config.json"
fi

osacompile -o "$APP" "$SRC/任务同步.applescript"
say "已生成按钮：$APP"
say ""
say "使用方法：打开「应用程序」文件夹，把「任务同步.app」拖到程序坞（Dock）。"
say "之后每次点一下图标，就会把当前任务与对话同步到手机。"
