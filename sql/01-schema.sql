-- 01-schema.sql —— 表结构
-- 在 WorkBuddy 云应用的 SQL 控制台执行（或通过平台的 migrate 能力）。

-- 每台电脑一行
CREATE TABLE IF NOT EXISTS wb_host (
  host_id           TEXT PRIMARY KEY,
  owner_id          TEXT,                       -- 绑定后写入 auth.uid()
  bind_code_hash    TEXT,                       -- 一次性绑定码的 md5，绑定后置空
  ingest_token_hash TEXT NOT NULL,              -- 设备令牌的 md5，明文不落库
  host_name         TEXT,
  platform          TEXT,
  agent_version     TEXT,
  bound_at          TIMESTAMPTZ,
  last_seen         TIMESTAMPTZ,
  stats             JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 每个任务一行；(host_id, task_uid) 幂等 upsert 的唯一约束
CREATE TABLE IF NOT EXISTS wb_task (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  host_id          TEXT NOT NULL,
  owner_id         TEXT,
  kind             TEXT NOT NULL DEFAULT 'session',  -- session | automation
  task_uid         TEXT NOT NULL,                     -- 会话 id 或 auto:<id>
  title            TEXT,
  status           TEXT,
  workspace        TEXT,                               -- 仅末级目录名，不含完整路径
  model            TEXT,
  mode             TEXT,
  msg_count        INTEGER NOT NULL DEFAULT 0,
  preview          TEXT,                               -- 最后一条助手输出，截断
  transcript       JSONB,                              -- [{r:'u'|'a', t, ts}]
  transcript_count INTEGER,
  transcript_at    TIMESTAMPTZ,
  created_at       TIMESTAMPTZ,
  updated_at       TIMESTAMPTZ,
  last_activity_at TIMESTAMPTZ,
  synced_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (host_id, task_uid)
);

CREATE INDEX IF NOT EXISTS idx_wb_task_owner_updated ON wb_task (owner_id, updated_at DESC);
