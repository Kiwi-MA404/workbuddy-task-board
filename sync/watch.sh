#!/bin/sh
# WorkBuddy 退出后自动补传一次。
#
# 由 launchd 每 2 分钟唤起一次，本身不常驻、不轮询数据库：
#   1. App 仍在运行        -> 立即退出（不读库、不联网）
#   2. 数据自上次同步未变化 -> 立即退出
#   3. 两者都满足（刚退出） -> 调一次 sync.py，然后退出
#
# 单次开销约十几毫秒，全部是进程探测与一次 stat。

set -u

BASE="${WB_SYNC_HOME:-$HOME/.wb-sync}"
DB="$HOME/.workbuddy/workbuddy.db"
STATE="$BASE/state"
APP_PATTERN="${WB_APP_PATTERN:-WorkBuddy.app/Contents/MacOS/}"

[ -x /usr/bin/python3 ] || exit 0
[ -f "$BASE/sync.py" ] || exit 0
[ -f "$DB" ] || exit 0

# App 还在跑：真正的同步交给按钮，这里不动
if pgrep -f "$APP_PATTERN" >/dev/null 2>&1; then
  exit 0
fi

CUR=$(stat -f %m "$DB" 2>/dev/null || echo 0)
LAST=$(cat "$STATE/last-db-mtime" 2>/dev/null || echo 0)
[ "$CUR" = "$LAST" ] && exit 0

mkdir -p "$STATE"
printf '%s [auto] WorkBuddy 已退出，补传一次（db mtime %s）\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S')" "$CUR" >> "$STATE/auto.log"
/usr/bin/python3 "$BASE/sync.py" >> "$STATE/auto.log" 2>&1 || true
