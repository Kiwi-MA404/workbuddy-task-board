#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""WorkBuddy 任务看板 —— 手动同步脚本（无第三方依赖，仅用 macOS 自带 Python 3）

点击「任务同步」按钮时执行一次：
  读取本地 WorkBuddy 数据 → 组装快照 → 上传云端 → 打印结果

不常驻、不定时、不联网除非被点击。
"""

import base64
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
BASE = os.environ.get("WB_SYNC_HOME", os.path.join(HOME, ".wb-sync"))
CONFIG_PATH = os.path.join(BASE, "config.json")
CACHE_PATH = os.path.join(BASE, "cache.json")
LOG_PATH = os.path.join(BASE, "sync.log")

DB_PATH = os.path.join(HOME, ".workbuddy", "workbuddy.db")
PROJECTS_DIR = os.path.join(HOME, ".workbuddy", "projects")

MAX_TRANSCRIPT_CHARS = 400000
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
    return query(
        """
        SELECT id, cwd,
               COALESCE(NULLIF(custom_title,''), NULLIF(title,''), '(未命名任务)') AS title,
               status, model, mode, created_at, updated_at, last_activity_at
        FROM sessions
        WHERE (deleted_at IS NULL OR deleted_at <= 0)
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


def collect(cache):
    sessions = read_sessions()
    automations = read_automations()
    files = index_jsonl()

    tasks = []
    transcripts = []
    for row in sessions:
        path = files.get(row["id"])
        info, messages = inspect(path, cache) if path else (None, None)
        if messages:
            transcripts.append({"uid": row["id"], "path": path, "messages": messages})
        status = str(row.get("status") or "pending").lower()
        cwd = row.get("cwd") or ""
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
        "agent_version": "3.0.0-manual",
        "stats": {
            "total": sum(1 for t in tasks if t["kind"] == "session"),
            "working": sum(1 for t in tasks if t["status"] == "working"),
            "completed": sum(1 for t in tasks if t["status"] == "completed"),
            "automations": sum(1 for t in tasks if t["kind"] == "automation"),
            "collected_at": datetime.now(tz=timezone.utc).isoformat(),
        },
        "tasks": tasks,
    }
    return snapshot, transcripts


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


def main():
    config = read_json(CONFIG_PATH, None)
    if not config or not config.get("endpoint") or not config.get("ingestToken"):
        fail("配置缺失：%s" % CONFIG_PATH)
    if not os.path.exists(DB_PATH):
        fail("找不到 WorkBuddy 数据：%s" % DB_PATH)

    cache = read_json(CACHE_PATH, {})
    snapshot, transcripts = collect(cache)

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

    write_json(CACHE_PATH, cache)

    summary = "%d 个任务" % task_count
    if transcripts:
        summary += " · %d 段对话" % synced
    if pruned:
        summary += " · 清理 %d 条已删任务" % pruned
    log("info", "同步完成：%s" % summary)
    print(summary if not failed else summary + "（%d 批对话失败）" % failed)


if __name__ == "__main__":
    main()
