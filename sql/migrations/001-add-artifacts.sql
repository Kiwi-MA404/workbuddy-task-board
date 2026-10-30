-- migrations/001-add-artifacts.sql —— v1.0.0 → v1.1.0 升级
--
-- 已有实例只需执行本文件，然后重新执行 sql/02-functions.sql
-- （wb_ingest 需要新版本才会写入 artifacts 字段）。
-- 全新安装无需本文件，sql/01-schema.sql 已包含该列。

ALTER TABLE wb_task ADD COLUMN IF NOT EXISTS artifacts JSONB NOT NULL DEFAULT '[]'::jsonb;
