-- 04-bootstrap-host.sql —— 登记一台电脑并拿到一串凭据
--
-- 用法：
--   1. 生成三个随机值（终端执行）：
--        uuidgen                        -> HOST_ID
--        openssl rand -hex 24           -> INGEST_TOKEN（48 位十六进制）
--        LC_ALL=C tr -dc 'A-Z2-9' </dev/urandom | head -c 10   -> BIND_CODE
--   2. 把下面的占位符替换成实际值后执行本文件。
--   3. 把 HOST_ID / INGEST_TOKEN / 云应用地址写进电脑端 ~/.wb-sync/config.json，
--      BIND_CODE 留给手机首次绑定时输入（只存 md5，用完即失效）。
--
-- 三个值都不需要设置过期时间：设备令牌可随时吊销（删掉 wb_host 行即可）。

INSERT INTO wb_host (host_id, ingest_token_hash, bind_code_hash, host_name, platform, agent_version)
VALUES (
  'REPLACE_WITH_HOST_UUID',
  md5('REPLACE_WITH_INGEST_TOKEN'),
  md5('REPLACE_WITH_BIND_CODE'),
  'My MacBook',
  'darwin',
  '3.0.0-manual'
);

-- 吊销一台设备（同时让它读不到任何数据，因为行没了）：
-- DELETE FROM wb_host WHERE host_id = 'REPLACE_WITH_HOST_UUID';
-- 重置绑定（换账号）：UPDATE wb_host SET owner_id = NULL, bind_code_hash = md5('NEWCODE') WHERE host_id = '...';
