-- migrations/002-add-files.sql —— v1.2.0 → v1.3.0 升级
--
-- 新增产物文件表与写入函数。可重复执行（IF NOT EXISTS / CREATE OR REPLACE）。
-- 全新安装无需本文件，sql/05-files.sql 内容相同。

--
-- 为什么不用对象存储：云存储只对已登录用户开放写入，而采集端是「匿名 + 设备令牌」，
-- 拿不到会话。文件量又很小（150 个可读产物合计 5.7 MB），因此以 base64 文本存入数据库，
-- 由 RLS 按 owner_id 隔离读取。单文件上限由采集端控制在 4 MB。

CREATE TABLE IF NOT EXISTS wb_file (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  host_id     TEXT NOT NULL,
  owner_id    TEXT,
  task_uid    TEXT NOT NULL,               -- 所属任务（= wb_task.task_uid）
  name        TEXT NOT NULL,               -- 产物文件名（任务内唯一）
  mime        TEXT,
  size        INTEGER NOT NULL DEFAULT 0,
  sha256      TEXT,                        -- 内容指纹，采集端据此跳过重传
  content_b64 TEXT NOT NULL,               -- base64 正文（图片 / 文本 / 音频）
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (host_id, task_uid, name)
);

CREATE INDEX IF NOT EXISTS idx_wb_file_owner_task ON wb_file (owner_id, task_uid);

ALTER TABLE wb_file ENABLE ROW LEVEL SECURITY;

GRANT SELECT ON TABLE public.wb_file TO authenticated;

DROP POLICY IF EXISTS wb_file_read_own ON wb_file;
CREATE POLICY wb_file_read_own ON wb_file
  FOR SELECT TO authenticated
  USING (owner_id = auth.uid() AND btrim(auth.uid()) NOT IN ('', 'anon'));

-- ---------------------------------------------------------------- 写入
-- 与 wb_ingest 同一条通路：设备令牌鉴权，只写不读。
-- replace 字段用于清理：某任务重新同步时，删掉本任务下不在清单里的旧文件。
CREATE OR REPLACE FUNCTION wb_ingest_files(p_token text, p_payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  v_host_id text;
  v_owner text;
  v_item jsonb;
  v_task jsonb;
  v_n integer := 0;
  v_pruned integer := 0;
BEGIN
  SELECT host_id, owner_id INTO v_host_id, v_owner
    FROM wb_host WHERE ingest_token_hash = md5(p_token);
  IF v_host_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'invalid_token');
  END IF;
  IF v_owner IS NULL OR btrim(v_owner) IN ('', 'anon') THEN
    v_owner := NULL;
  END IF;

  FOR v_item IN SELECT value FROM jsonb_array_elements(COALESCE(p_payload->'items','[]'::jsonb)) LOOP
    IF COALESCE(v_item->>'task_uid','') <> '' AND COALESCE(v_item->>'name','') <> '' THEN
      INSERT INTO wb_file (host_id, owner_id, task_uid, name, mime, size, sha256, content_b64, updated_at)
      VALUES (v_host_id, v_owner, v_item->>'task_uid', v_item->>'name',
              v_item->>'mime', COALESCE((v_item->>'size')::int, 0),
              v_item->>'sha', v_item->>'b64', now())
      ON CONFLICT (host_id, task_uid, name) DO UPDATE SET
        mime = EXCLUDED.mime,
        size = EXCLUDED.size,
        sha256 = EXCLUDED.sha256,
        content_b64 = EXCLUDED.content_b64,
        updated_at = now(),
        owner_id = COALESCE(NULLIF(wb_file.owner_id,''), EXCLUDED.owner_id);
      v_n := v_n + 1;
    END IF;
  END LOOP;

  FOR v_task IN SELECT value FROM jsonb_array_elements(COALESCE(p_payload->'replace','[]'::jsonb)) LOOP
    DELETE FROM wb_file
     WHERE host_id = v_host_id
       AND task_uid = v_task->>'uid'
       AND name NOT IN (SELECT jsonb_array_elements_text(COALESCE(v_task->'names','[]'::jsonb)));
    v_pruned := v_pruned + 1;
  END LOOP;

  UPDATE wb_host SET last_seen = now() WHERE host_id = v_host_id;
  RETURN jsonb_build_object('ok', true, 'uploaded', v_n, 'replaced', v_pruned);
END $fn$;

REVOKE EXECUTE ON FUNCTION wb_ingest_files(text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION wb_ingest_files(text, jsonb) TO anon, authenticated;
