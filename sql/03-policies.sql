-- 03-policies.sql —— 权限与行级安全
-- 读：只有已登录用户、且行属于自己；写：只通过 SECURITY DEFINER 函数。

ALTER TABLE wb_host ENABLE ROW LEVEL SECURITY;
ALTER TABLE wb_task ENABLE ROW LEVEL SECURITY;

-- 前端只读，不授予任何写权限
GRANT SELECT ON TABLE public.wb_host TO authenticated;
GRANT SELECT ON TABLE public.wb_task TO authenticated;

DROP POLICY IF EXISTS wb_host_read_own ON wb_host;
CREATE POLICY wb_host_read_own ON wb_host
  FOR SELECT TO authenticated
  USING (owner_id = auth.uid() AND btrim(auth.uid()) NOT IN ('', 'anon'));

DROP POLICY IF EXISTS wb_task_read_own ON wb_task;
CREATE POLICY wb_task_read_own ON wb_task
  FOR SELECT TO authenticated
  USING (owner_id = auth.uid() AND btrim(auth.uid()) NOT IN ('', 'anon'));

-- 先收回 PUBLIC 的默认执行权，再按需授予
REVOKE EXECUTE ON FUNCTION wb_ingest(text, jsonb) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION wb_ingest_transcripts(text, jsonb) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION wb_bind(text) FROM PUBLIC;

-- 写入函数给匿名角色：本机脚本没有登录态，靠设备令牌自证
GRANT EXECUTE ON FUNCTION wb_ingest(text, jsonb) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION wb_ingest_transcripts(text, jsonb) TO anon, authenticated;

-- 绑定必须已登录
GRANT EXECUTE ON FUNCTION wb_bind(text) TO authenticated;
