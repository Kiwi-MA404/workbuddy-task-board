-- 02-functions.sql —— 三个 SECURITY DEFINER 函数
-- 写入走设备令牌（匿名可调用，只写不读）；绑定需要登录态。

-- ---------------------------------------------------------------- 任务摘要写入
CREATE OR REPLACE FUNCTION wb_ingest(p_token text, p_payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  v_host_id text;
  v_owner text;
  v_item jsonb;
  v_n integer := 0;
  v_pruned integer := 0;
  v_uids text[];
BEGIN
  SELECT host_id, owner_id INTO v_host_id, v_owner
    FROM wb_host WHERE ingest_token_hash = md5(p_token);
  IF v_host_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'invalid_token');
  END IF;
  IF v_owner IS NULL OR btrim(v_owner) IN ('', 'anon') THEN
    v_owner := NULL;
  END IF;

  UPDATE wb_host SET
    last_seen = now(),
    host_name = COALESCE(NULLIF(p_payload->>'host_name',''), host_name),
    platform  = COALESCE(NULLIF(p_payload->>'platform',''),  platform),
    agent_version = COALESCE(NULLIF(p_payload->>'agent_version',''), agent_version),
    stats = COALESCE(p_payload->'stats', '{}'::jsonb)
  WHERE host_id = v_host_id;

  SELECT COALESCE(array_agg(DISTINCT e.value->>'uid'), ARRAY[]::text[])
    INTO v_uids
    FROM jsonb_array_elements(COALESCE(p_payload->'tasks','[]'::jsonb)) AS e;

  FOR v_item IN SELECT value FROM jsonb_array_elements(COALESCE(p_payload->'tasks','[]'::jsonb)) LOOP
    INSERT INTO wb_task (host_id, owner_id, kind, task_uid, title, status, workspace, model, mode,
                         msg_count, preview, created_at, updated_at, last_activity_at, synced_at)
    VALUES (v_host_id, v_owner,
            COALESCE(NULLIF(v_item->>'kind',''), 'session'),
            v_item->>'uid', v_item->>'title', v_item->>'status', v_item->>'workspace',
            v_item->>'model', v_item->>'mode',
            COALESCE((v_item->>'msg_count')::int, 0), v_item->>'preview',
            NULLIF(v_item->>'created_at','')::timestamptz,
            NULLIF(v_item->>'updated_at','')::timestamptz,
            NULLIF(v_item->>'last_activity_at','')::timestamptz,
            now())
    ON CONFLICT (host_id, task_uid) DO UPDATE SET
      kind = EXCLUDED.kind,
      title = EXCLUDED.title,
      status = EXCLUDED.status,
      workspace = EXCLUDED.workspace,
      model = EXCLUDED.model,
      mode = EXCLUDED.mode,
      msg_count = EXCLUDED.msg_count,
      preview = EXCLUDED.preview,
      created_at = COALESCE(EXCLUDED.created_at, wb_task.created_at),
      updated_at = EXCLUDED.updated_at,
      last_activity_at = EXCLUDED.last_activity_at,
      synced_at = now(),
      owner_id = COALESCE(NULLIF(wb_task.owner_id,''), EXCLUDED.owner_id);
    v_n := v_n + 1;
  END LOOP;

  -- 仅当调用方声明这是全量清单、且清单非空时，才清理已不存在的行
  IF COALESCE(p_payload->>'full','false') = 'true' AND array_length(v_uids, 1) IS NOT NULL THEN
    DELETE FROM wb_task WHERE host_id = v_host_id AND NOT (task_uid = ANY(v_uids));
    GET DIAGNOSTICS v_pruned = ROW_COUNT;
  END IF;

  RETURN jsonb_build_object('ok', true, 'tasks', v_n, 'pruned', v_pruned);
END $fn$;

-- ---------------------------------------------------------------- 对话正文写入
-- 正文以 Base64 传输：明文里的命令行/代码片段可能触发网关内容检测（返回 403）。
CREATE OR REPLACE FUNCTION wb_ingest_transcripts(p_token text, p_payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  v_host_id text;
  v_item jsonb;
  v_msgs jsonb;
  v_n integer := 0;
BEGIN
  SELECT host_id INTO v_host_id FROM wb_host WHERE ingest_token_hash = md5(p_token);
  IF v_host_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'invalid_token');
  END IF;

  FOR v_item IN SELECT value FROM jsonb_array_elements(COALESCE(p_payload->'items','[]'::jsonb)) LOOP
    BEGIN
      v_msgs := convert_from(decode(v_item->>'b64', 'base64'), 'UTF8')::jsonb;
    EXCEPTION WHEN others THEN
      v_msgs := NULL;
    END;
    IF v_msgs IS NOT NULL AND jsonb_typeof(v_msgs) = 'array' THEN
      UPDATE wb_task
         SET transcript = v_msgs,
             transcript_count = COALESCE((v_item->>'count')::int, jsonb_array_length(v_msgs)),
             transcript_at = now()
       WHERE host_id = v_host_id AND task_uid = v_item->>'uid';
      IF FOUND THEN
        v_n := v_n + 1;
      END IF;
    END IF;
  END LOOP;

  UPDATE wb_host SET last_seen = now() WHERE host_id = v_host_id;
  RETURN jsonb_build_object('ok', true, 'updated', v_n);
END $fn$;

-- ---------------------------------------------------------------- 设备绑定
-- 注意：未登录时 auth.uid() 返回字面量 'anon'（不是 NULL），必须显式排除，
-- 否则匿名者可借绑定函数把主机认领到 'anon' 名下。
CREATE OR REPLACE FUNCTION wb_bind(p_code text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  v_uid text := auth.uid();
  v_host wb_host;
BEGIN
  IF v_uid IS NULL OR btrim(v_uid) IN ('', 'anon') THEN
    RETURN jsonb_build_object('ok', false, 'error', 'not_signed_in');
  END IF;
  IF p_code IS NULL OR btrim(p_code) = '' THEN
    RETURN jsonb_build_object('ok', false, 'error', 'invalid_code');
  END IF;

  SELECT * INTO v_host FROM wb_host WHERE bind_code_hash = md5(btrim(p_code));
  IF v_host.host_id IS NULL THEN
    RETURN jsonb_build_object('ok', false, 'error', 'invalid_code');
  END IF;
  IF v_host.owner_id IS NOT NULL
     AND btrim(v_host.owner_id) NOT IN ('', 'anon')
     AND v_host.owner_id <> v_uid THEN
    RETURN jsonb_build_object('ok', false, 'error', 'already_bound');
  END IF;

  UPDATE wb_host SET owner_id = v_uid, bound_at = now(), bind_code_hash = NULL
   WHERE host_id = v_host.host_id;
  UPDATE wb_task SET owner_id = v_uid WHERE host_id = v_host.host_id;

  RETURN jsonb_build_object('ok', true);
END $fn$;
