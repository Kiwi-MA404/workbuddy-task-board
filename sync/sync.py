#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""WorkBuddy 任务看板 —— 手动同步脚本（无第三方依赖，仅用 macOS 自带 Python 3）

点击「任务同步」按钮时执行一次：
  读取本地 WorkBuddy 数据 → 组装快照 → 上传云端 → 打印结果

不常驻、不定时、不联网除非被点击。
"""

import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
BASE = os.environ.get("WB_SYNC_HOME", os.path.join(HOME, ".wb-sync"))
CONFIG_PATH = os.path.join(BASE, "config.json")
CACHE_PATH = os.path.join(BASE, "cache.json")
LOG_PATH = os.path.join(BASE, "sync.log")

DB_PATH = os.path.join(HOME, ".workbuddy", "workbuddy.db")
PROJECTS_DIR = os.path.join(HOME, ".workbuddy", "projects")
ARTIFACT_DIR = os.path.join(HOME, ".workbuddy", "artifact-index")
STATE_DIR = os.path.join(BASE, "state")

MAX_TRANSCRIPT_CHARS = 400000
ARTIFACT_MAX = 12           # 每个任务最多同步的产物条数（取最近更新的）
FILE_MAX_BYTES = 4 * 1024 * 1024   # 单文件上限：超过则只同步元数据
FILE_BATCH_BYTES = 3 * 1024 * 1024  # 单次上传的原始字节上限（base64 后约 4 MB）
FILE_TOTAL_BYTES = 80 * 1024 * 1024  # 单次运行的总量上限，防止异常情况刷量
DB_MTIME_MARK = os.path.join(STATE_DIR, "last-db-mtime")
SYSTEM_RE = re.compile(r"<system-reminder[\s\S]*?</system-reminder>", re.I)
USER_QUERY_RE = re.compile(r"</?user_query>", re.I)


# ---------------------------------------------------------------- 基础设施

def log(level, message):
    line = "%s [%s] %s" % (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), level, message)
    try:
        os.makedirs(BASE, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 256 * 1024:
            open(LOG_PATH, "w").close()
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    return line


def fail(message):
    log("error", message)
    print(message)
    sys.exit(1)


def read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return fallback


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False)
    os.replace(tmp, path)


def iso(ms):
    try:
        value = float(ms)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------- 数据采集

def query(sql):
    uri = "file:%s?mode=ro" % DB_PATH
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def read_sessions():
    # 只同步本机会话：transport='cloud' 的任务在云端运行，本地没有会话文件，
    # 同步上去只有标题、点开是空的，反而干扰阅读。
    return query(
        """
        SELECT id, cwd,
               COALESCE(NULLIF(custom_title,''), NULLIF(title,''), '(未命名任务)') AS title,
               status, model, mode, created_at, updated_at, last_activity_at
        FROM sessions
        WHERE (deleted_at IS NULL OR deleted_at <= 0)
          AND COALESCE(transport, 'local') <> 'cloud'
        ORDER BY updated_at DESC LIMIT 400
        """
    )


def read_automations():
    try:
        return query(
            """
            SELECT id, name, status, schedule_type, next_run_at, last_run_at, updated_at
            FROM automations WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT 100
            """
        )
    except sqlite3.Error:
        return []


def index_jsonl():
    mapping = {}
    try:
        entries = os.listdir(PROJECTS_DIR)
    except OSError:
        return mapping
    for name in entries:
        folder = os.path.join(PROJECTS_DIR, name)
        if not os.path.isdir(folder):
            continue
        try:
            files = os.listdir(folder)
        except OSError:
            continue
        for fname in files:
            if fname.endswith(".jsonl") and "rollback" not in fname:
                mapping[fname[:-6]] = os.path.join(folder, fname)
    return mapping


def build_transcript(raw):
    """解析为 [{r:'u'|'a', t, ts}]，剔除注入的系统上下文"""
    messages = []
    chars = 0
    for line in raw.split("\n"):
        if '"type":"message"' not in line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") != "message":
            continue
        role = obj.get("role")
        if role not in ("user", "assistant"):
            continue
        blocks = obj.get("content") or []
        text = "\n".join(
            b.get("text", "") for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
        if not text.strip():
            continue
        if role == "user":
            text = SYSTEM_RE.sub("", text)
            text = USER_QUERY_RE.sub("", text)
        text = text.strip()
        if not text:
            continue
        chars += len(text)
        if chars > MAX_TRANSCRIPT_CHARS:
            break
        messages.append({"r": "u" if role == "user" else "a", "t": text, "ts": obj.get("timestamp")})
    return messages


def inspect(path, cache):
    """返回 (统计信息, 正文或 None)；正文仅在文件变化时解析"""
    try:
        st = os.stat(path)
    except OSError:
        return None, None
    cached = cache.get(path)
    if cached and cached.get("mtime") == st.st_mtime and cached.get("size") == st.st_size:
        return cached, None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None, None

    count = raw.count('"type":"message"')
    messages = build_transcript(raw)
    last_ai = None
    for msg in reversed(messages):
        if msg["r"] == "a":
            last_ai = msg["t"]
            break
    entry = {
        "mtime": st.st_mtime,
        "size": st.st_size,
        "count": count,
        "preview": " ".join(last_ai.split())[:240] if last_ai else None,
    }
    cache[path] = entry
    return entry, messages


def read_artifacts(session_id, cache):
    """读取该会话的产物清单（只取元数据，不含文件内容与完整路径）"""
    path = os.path.join(ARTIFACT_DIR, session_id + ".json")
    try:
        st = os.stat(path)
    except OSError:
        return []
    cached = cache.get("artifact:" + path)
    if cached and cached.get("mtime") == st.st_mtime and cached.get("size") == st.st_size:
        return cached.get("items", [])
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []

    items = []
    for a in data.get("artifacts") or []:
        if not isinstance(a, dict):
            continue
        name = a.get("title") or a.get("name") or ""
        if not name:
            continue
        uri = a.get("uri") or ""
        local = urllib.parse.unquote(uri[7:]) if uri.startswith("file://") else ""
        items.append(
            {
                "n": name,
                "k": a.get("contentType") or a.get("type") or "file",
                "m": a.get("mimeType") or "",
                "s": int(a.get("size") or 0),
                "t": a.get("updatedAt") or a.get("createdAt") or 0,
                "p": local,  # 仅本机使用，上传前剔除
            }
        )
    items.sort(key=lambda x: x.get("t") or 0, reverse=True)
    items = items[:ARTIFACT_MAX]
    cache["artifact:" + path] = {"mtime": st.st_mtime, "size": st.st_size, "items": items}
    return items


def collect(cache):
    sessions = read_sessions()
    automations = read_automations()
    files = index_jsonl()

    tasks = []
    transcripts = []
    file_plan = []
    for row in sessions:
        path = files.get(row["id"])
        info, messages = inspect(path, cache) if path else (None, None)
        if messages:
            transcripts.append({"uid": row["id"], "path": path, "messages": messages})
        status = str(row.get("status") or "pending").lower()
        cwd = row.get("cwd") or ""
        arts = read_artifacts(row["id"], cache)
        if arts:
            file_plan.append({"uid": row["id"], "artifacts": arts})
        tasks.append(
            {
                "uid": row["id"],
                "kind": "session",
                "title": row.get("title"),
                "status": status,
                "workspace": os.path.basename(cwd) if cwd else None,
                "model": row.get("model"),
                "mode": row.get("mode"),
                "msg_count": len(messages) if messages else (info.get("count", 0) if info else 0),
                "preview": info.get("preview") if info else None,
                # 本地路径不随快照上传
                "artifacts": [{k: v for k, v in it.items() if k != "p"} for it in arts],
                "created_at": iso(row.get("created_at")),
                "updated_at": iso(row.get("updated_at")),
                "last_activity_at": iso(row.get("last_activity_at") or row.get("updated_at")),
            }
        )

    for row in automations:
        tasks.append(
            {
                "uid": "auto:%s" % row["id"],
                "kind": "automation",
                "title": row.get("name") or "(未命名定时任务)",
                "status": "scheduled" if row.get("status") == "ACTIVE" else str(row.get("status") or "").lower(),
                "workspace": None,
                "model": None,
                "mode": row.get("schedule_type"),
                "msg_count": 0,
                "preview": ("下次运行 " + iso(row.get("next_run_at"))) if row.get("next_run_at") else None,
                "created_at": None,
                "updated_at": iso(row.get("updated_at")),
                "last_activity_at": iso(row.get("last_run_at")),
            }
        )

    snapshot = {
        # 全量清单标记：服务端据此清理「电脑上已不存在」的行。
        # 若将来改为增量推送，去掉此标记即可避免误删。
        "full": True,
        "host_name": os.uname().nodename.replace(".local", ""),
        "platform": "darwin %s %s" % (os.uname().release, os.uname().machine),
        "agent_version": "3.2.0-manual",
        "stats": {
            "total": sum(1 for t in tasks if t["kind"] == "session"),
            "working": sum(1 for t in tasks if t["status"] == "working"),
            "completed": sum(1 for t in tasks if t["status"] == "completed"),
            "automations": sum(1 for t in tasks if t["kind"] == "automation"),
            "collected_at": datetime.now(tz=timezone.utc).isoformat(),
        },
        "tasks": tasks,
    }
    return snapshot, transcripts, file_plan


# ---------------------------------------------------------------- 上传

def rpc(config, function, payload, retries=2):
    url = "%s/.cloud/database/rest/rpc/%s" % (config["endpoint"].rstrip("/"), function)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "content-type": "application/json",
                    "x-wb-webapp-access-key": config["publishableKey"],
                },
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                text = resp.read().decode("utf-8", "replace")
            data = json.loads(text)
            # 网关直接返回存储过程的 jsonb 结果，没有额外信封
            if isinstance(data, dict):
                return data
            last = "响应格式异常"
        except urllib.error.HTTPError as exc:
            last = "HTTP %s" % exc.code
        except (urllib.error.URLError, ValueError, OSError) as exc:
            last = str(exc)[:160]
        if attempt + 1 < retries:
            time.sleep(1.5)
    raise RuntimeError(last or "未知错误")


def chunk(items, max_items=3, max_chars=700000):
    batches = []
    current = []
    chars = 0
    for item in items:
        size = sum(len(m["t"]) for m in item["messages"])
        if current and (len(current) >= max_items or chars + size > max_chars):
            batches.append(current)
            current = []
            chars = 0
        current.append(item)
        chars += size
    if current:
        batches.append(current)
    return batches


# ---------------------------------------------------------------- 产物文件

def plan_files(file_plan, cache):
    """挑出需要上传的文件；已按 mtime+大小+归属校验过的跳过。

    返回 (uploads, replace)：
      uploads  待上传项（含原始字节，仅内存中）
      replace  需要清理旧文件的任务清单（本任务下不在 names 里的将被删除）
    """
    uploads = []
    replace = []
    budget = FILE_TOTAL_BYTES

    for task in file_plan:
        names = []
        changed = False
        for art in task["artifacts"]:
            name = art["n"]
            names.append(name)
            path = art.get("p") or ""
            if not path or not os.path.isfile(path):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_size > FILE_MAX_BYTES:
                continue

            key = "file:" + path
            cached = cache.get(key)
            if (
                cached
                and cached.get("mtime") == st.st_mtime
                and cached.get("size") == st.st_size
                and cached.get("uid") == task["uid"]
                and cached.get("name") == name
            ):
                continue
            if st.st_size > budget:
                log("warn", "本次总量已达上限，剩余文件留待下次：%s" % name)
                break
            try:
                with open(path, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue

            budget -= len(raw)
            changed = True
            uploads.append(
                {
                    "uid": task["uid"],
                    "name": name,
                    "mime": art.get("m") or "application/octet-stream",
                    "size": len(raw),
                    "sha": hashlib.sha256(raw).hexdigest(),
                    "raw": raw,
                    "_key": key,
                    "_mtime": st.st_mtime,
                    "_size": st.st_size,
                }
            )

        name_key = "names:" + task["uid"]
        if names and cache.get(name_key) != names:
            replace.append({"uid": task["uid"], "names": names})
        cache[name_key] = names

    return uploads, replace


def send_files(config, uploads, replace):
    """一次上传调用；返回服务端结果"""
    items = [
        {
            "task_uid": it["uid"],
            "name": it["name"],
            "mime": it["mime"],
            "size": it["size"],
            "sha": it["sha"],
            "b64": base64.b64encode(it["raw"]).decode("ascii"),
        }
        for it in uploads
    ]
    payload = {"items": items}
    if replace:
        payload["replace"] = replace
    return rpc(config, "wb_ingest_files", {"p_token": config["ingestToken"], "p_payload": payload})


def push_files(config, uploads, replace, cache):
    """按体积分批；返回 (成功文件数, 失败批数, 清理任务数)"""
    state = {"done": 0, "failed": 0, "pruned": 0, "first": True}
    pending_replace = list(replace)
    batch = []
    size = 0

    def flush(current):
        if not current and not (state["first"] and pending_replace):
            return
        try:
            res = send_files(config, current, pending_replace if state["first"] else None)
            state["done"] += res.get("uploaded", len(current))
            if state["first"]:
                state["pruned"] = res.get("replaced", len(pending_replace))
                pending_replace.clear()
                state["first"] = False
            for it in current:
                cache[it["_key"]] = {
                    "mtime": it["_mtime"],
                    "size": it["_size"],
                    "uid": it["uid"],
                    "name": it["name"],
                }
        except RuntimeError as exc:
            state["failed"] += 1
            for it in current:
                cache.pop(it["_key"], None)
            log("error", "文件上传失败（%d 个）：%s" % (len(current), exc))

    for item in uploads:
        if batch and (size + item["size"] > FILE_BATCH_BYTES or len(batch) >= 8):
            flush(batch)
            batch = []
            size = 0
        batch.append(item)
        size += item["size"]
    flush(batch)

    return state["done"], state["failed"], state["pruned"]


def mark_synced():
    """记录本次同步时的数据库版本，供「退出后自动上传」的守卫判重"""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(DB_MTIME_MARK, "w", encoding="utf-8") as fh:
            fh.write(str(int(os.stat(DB_PATH).st_mtime)))
    except OSError:
        pass


def main():
    config = read_json(CONFIG_PATH, None)
    if not config or not config.get("endpoint") or not config.get("ingestToken"):
        fail("配置缺失：%s" % CONFIG_PATH)
    if not os.path.exists(DB_PATH):
        fail("找不到 WorkBuddy 数据：%s" % DB_PATH)

    cache = read_json(CACHE_PATH, {})
    # 缓存结构升级：v3.2.0 起产物条目带本地路径，旧条目需失效重算
    if cache.get("_schema") != 2:
        for key in [k for k in list(cache) if k.startswith("artifact:") or k.startswith("names:")]:
            cache.pop(key, None)
        cache["_schema"] = 2
    snapshot, transcripts, file_plan = collect(cache)

    try:
        result = rpc(
            config,
            "wb_ingest",
            {"p_token": config["ingestToken"], "p_payload": snapshot},
        )
        if result.get("ok") is not True:
            fail("上传被拒绝：%s" % (result.get("error") or "未知原因"))
        task_count = result.get("tasks", len(snapshot["tasks"]))
        pruned = result.get("pruned") or 0
        log("info", "任务摘要已上传：%s 条%s" % (task_count, "，清理已删任务 %s 条" % pruned if pruned else ""))
    except RuntimeError as exc:
        fail("上传失败：%s" % exc)

    synced = 0
    failed = 0
    for batch in chunk(transcripts):
        items = [
            {
                "uid": it["uid"],
                "count": len(it["messages"]),
                "b64": base64.b64encode(
                    json.dumps(it["messages"], ensure_ascii=False).encode("utf-8")
                ).decode("ascii"),
            }
            for it in batch
        ]
        try:
            res = rpc(config, "wb_ingest_transcripts", {"p_token": config["ingestToken"], "p_payload": {"items": items}})
            synced += res.get("updated", len(batch))
        except RuntimeError as exc:
            failed += 1
            for it in batch:
                cache.pop(it["path"], None)
            log("error", "对话正文上传失败（%d 条）：%s" % (len(batch), exc))

    # 产物文件：只传内容有变化的，单文件 ≤4MB（更小的体积换来随时可看）
    file_done = 0
    file_failed = 0
    file_pruned = 0
    try:
        uploads, replace = plan_files(file_plan, cache)
        if uploads or replace:
            file_done, file_failed, file_pruned = push_files(config, uploads, replace, cache)
            if file_done:
                log("info", "产物文件已上传：%s 个" % file_done)
    except Exception as exc:  # 文件失败不应影响任务与对话的同步
        file_failed += 1
        log("error", "产物文件处理异常：%s" % exc)

    write_json(CACHE_PATH, cache)
    mark_synced()

    summary = "%d 个任务" % task_count
    if transcripts:
        summary += " · %d 段对话" % synced
    if file_done:
        summary += " · %d 个文件" % file_done
    if pruned:
        summary += " · 清理 %d 条已删任务" % pruned
    log("info", "同步完成：%s" % summary)
    print(summary if not (failed or file_failed) else summary + "（%d 批失败）" % (failed + file_failed))


if __name__ == "__main__":
    main()
