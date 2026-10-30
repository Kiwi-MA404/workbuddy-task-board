# Changelog

## v1.1.0 —— 2026-10-30

### 新增

- **程序退出时自动执行一次上传**：`sync/install.sh --auto` 安装一个 launchd 定时器，每 2 分钟唤起一次 `sync/watch.sh`。守卫脚本在 WorkBuddy 运行期间立即退出（一次 `pgrep`，约 10 毫秒）；检测到 App 已退出且数据自上次同步后有变化时，才调一次 `sync.py`。无常驻进程、无数据库轮询。
- **产物查看**：任务详情页新增「产物」清单——名称、类型（代码 / 文档 / 媒体）、大小、更新时间；列表卡片同步显示产物数量。数据来自 `~/.workbuddy/artifact-index`，只上传元数据，文件本身与完整路径不上传。
- **滚动条常显**：翻看对话历史时右侧滚动条保持可见（`overflow-y: scroll` + 细滚动条样式；iOS 上系统滚动条仍会淡出，属平台限制）。

### 调整

- **移除右上角「检测中」标识**：数据由按钮或退出补传触发，「在线 / 离线」不再是有效判据，顶栏只保留数据时点。
- **不再同步云端任务**：`transport='cloud'` 的会话在云端运行、本地没有记录，同步上去只有标题、点开是空的，现已在采集端排除。既有实例在下次同步后会自动清理这些残留行（`full:true` 清理机制）。

### 升级（v1.0.0 → v1.1.0）

```sql
-- 云应用 SQL 控制台执行：
sql/migrations/001-add-artifacts.sql
sql/02-functions.sql          -- 重新执行，wb_ingest 需新版本才会写 artifacts
```

然后照常运行 `sync/install.sh`（或 `--auto`）更新本机脚本。

## v1.0.0 —— 2026-10-30

首次发布。详见 [Release v1.0.0](https://github.com/Kiwi-MA404/workbuddy-task-board/releases/tag/v1.0.0)。
