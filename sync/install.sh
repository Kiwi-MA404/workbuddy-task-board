#!/bin/bash
# 安装「任务同步」：一个可双击的按钮 + 一个纯 Python 同步脚本。
# 加 --auto 额外安装「WorkBuddy 退出后自动补传一次」的定时器
# （只是每 2 分钟一次的轻量守卫，App 运行期间立即退出，无常驻进程）。
set -euo pipefail

BASE="$HOME/.wb-sync"
APP="$HOME/Applications/任务同步.app"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO=0
[ "${1:-}" = "--auto" ] && AUTO=1

say() { printf '%s\n' "$*"; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "本脚本仅支持 macOS。"
[ -x /usr/bin/python3 ] || die "缺少 /usr/bin/python3（macOS 自带组件，请先安装命令行工具）。"

mkdir -p "$BASE" "$BASE/state" "$HOME/Applications"
chmod 700 "$BASE"

install -m 755 "$SRC/sync.py" "$BASE/sync.py"
install -m 755 "$SRC/watch.sh" "$BASE/watch.sh"
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

if [ "$AUTO" = "1" ]; then
  PLIST="$HOME/Library/LaunchAgents/cn.wbtb.quitsync.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  sed "s|__HOME__|$HOME|g" "$SRC/launchd.plist.in" > "$PLIST"
  say "已写入定时器：$PLIST"

  if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
    say "定时器已加载：WorkBuddy 退出后会自动补传一次。"
  else
    say ""
    say "定时器已就位，但当前会话无权加载。两种办法让其生效："
    say "  1) 立即生效，在「终端」执行一次："
    say "     launchctl bootstrap gui/\$(id -u) \"$PLIST\""
    say "  2) 不执行也可以，下次登录 macOS 会自动加载。"
  fi
fi

say ""
say "使用方法：打开「应用程序」文件夹，把「任务同步.app」拖到程序坞（Dock）。"
say "之后每次点一下图标，就会把当前任务与对话同步到手机。"
[ "$AUTO" = "1" ] && say "关闭 WorkBuddy 后，最迟 2 分钟会自动补传一次（数据有变化时）。"
