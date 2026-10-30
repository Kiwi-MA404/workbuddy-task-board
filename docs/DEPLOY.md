# 部署与排错

面向「从零搭一套自己的实例」。每一步都可以单独验证，出错时不会牵连下一步。

## 前置条件

| 项 | 要求 |
|---|---|
| WorkBuddy 桌面端 | 已登录，且能创建云应用 |
| 操作系统 | macOS（同步脚本依赖 `~/.workbuddy` 与 `/usr/bin/python3`） |
| 静态托管 | 任意静态站点服务，能自定义 `web/` 为根目录 |

不需要 Node、不需要构建步骤、不需要第三方 Python 包。

## 第 1 步：云应用

在 WorkBuddy 中创建一个云数据库应用。完成后你会得到：

- **应用地址**：`https://<前缀>.app.workbuddy.host`
- **publishable key**：`wbpk_<appId>_<随机串>`

这两个值会分别写进前端 `config.js` 和本机 `~/.wb-sync/config.json`。publishable key 本身就是公开凭据（前端必须内联），安全边界由行级安全保证，不靠它保密。

## 第 2 步：建库

在云应用控制台依次执行：

```sql
-- 顺序不可颠倒：函数依赖表，权限依赖函数
sql/01-schema.sql
sql/02-functions.sql
sql/03-policies.sql
```

验证（应返回 0 行，因为没有数据）：

```sql
SELECT count(*) FROM wb_task;
SELECT count(*) FROM wb_host;
```

再验证匿名身份拿不到数据（应返回 0 行，而不是报错）：

```sql
SELECT count(*) FROM wb_task;   -- 以 anon 身份执行
```

## 第 3 步：登记设备

```bash
HOST_ID=$(uuidgen)
TOKEN=$(openssl rand -hex 24)
CODE=$(LC_ALL=C tr -dc 'A-Z2-9' </dev/urandom | head -c 10)
echo "host_id : $HOST_ID"
echo "token   : $TOKEN"
echo "bind    : $CODE"
```

把三个值填进 `sql/04-bootstrap-host.sql` 执行。注意：

- **token 只在这里出现一次**，之后数据库只存它的 md5，丢了只能删行重来
- **bind code 也是一次性的**，绑定成功后字段被置空

## 第 4 步：前端

```bash
cp web/config.example.js web/config.js
# 编辑 web/config.js，填入 publishableKey
```

发布 `web/` 目录到静态托管。发布后自检：

```bash
BASE=https://你的应用地址
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/?sharecode=你的分享码"
curl -s "$BASE/config.js?sharecode=你的分享码" | head -3
```

第二条应能看到你的 `window.WB_CONFIG`。如果返回 403，说明少了 `?sharecode=`。

## 第 5 步：同步按钮

```bash
WB_ENDPOINT=https://你的应用地址 \
WB_PUBLISHABLE_KEY=wbpk_你的值 \
WB_INGEST_TOKEN=第 3 步的 token \
bash sync/install.sh
```

产物：

- `~/.wb-sync/sync.py`（同步脚本）
- `~/.wb-sync/config.json`（权限 600）
- `~/Applications/任务同步.app`（可双击的按钮）

手动验证一次：

```bash
/usr/bin/python3 ~/.wb-sync/sync.py
```

预期输出：`30 个任务 · 1 段对话`。日志在 `~/.wb-sync/sync.log`。

## 第 6 步：手机

1. 打开站点链接（**必须带 `?sharecode=`**，否则 403）
2. 用 WorkBuddy 账号登录（微信内置浏览器可能拦截第三方登录，建议用 Safari/Chrome）
3. 输入绑定码（第 3 步的 code）
4. 分享 → 添加到主屏幕

## 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 页面 403 / 空白 | 链接少了 `?sharecode=` | 用完整地址重新打开 |
| 白屏且控制台报 `publishableKey` 缺失 | `web/config.js` 未创建 | 复制模板并填写 |
| 登录后停在绑定页 | 该账号与主机 `owner_id` 不一致 | 数据库中先把 `owner_id` 置空再重新绑定 |
| 绑定提示 `invalid_code` | 码已用过 / 打错 | `UPDATE wb_host SET bind_code_hash = md5('新码')` |
| 点按钮报「找不到 WorkBuddy 数据」 | 未安装 WorkBuddy 或路径不同 | 确认 `~/.workbuddy/workbuddy.db` 存在 |
| 点按钮报权限错误 | 沙箱限制读取 | 系统设置 → 隐私与安全性 → 完全磁盘访问，授权终端 |
| 对话正文上传失败 | 网关内容检测拦截 | 已内置 Base64 传输；若仍失败看 `sync.log` |
| 面板上的任务比电脑上多 | 电脑上已删除的任务残留 | 正常不会发生，脚本带 `full:true` 会清理；手动删行即可 |
| 手机仍是旧界面 | 浏览器缓存 | 移除主屏幕图标后重新添加 |

## 卸载

```bash
# 停止使用（保留配置，可随时恢复）
rm -f ~/Applications/任务同步.app
rm -rf ~/.wb-sync

# 彻底清除：再到云应用控制台删除站点，并 DROP TABLE wb_task, wb_host
```

## 运维要点

- **换设备令牌**：`UPDATE wb_host SET ingest_token_hash = md5('新token') WHERE host_id='...'`，同时改 `~/.wb-sync/config.json`
- **吊销一台电脑**：`DELETE FROM wb_host WHERE host_id='...'`（关联任务行可一并删除）
- **数据体积**：对话正文约 600 KB / 30 个任务；`transcript` 是 JSONB，可按需清理旧任务的正文（`UPDATE wb_task SET transcript=NULL WHERE updated_at < now() - interval '90 days'`）
- **日志轮转**：`sync.log` 超过 256 KB 自动清空
