#!/usr/bin/env python3
"""Bounded read-only JSON evidence for conversational WeChat tools.

This module deliberately has no dependency on the report engine.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from message_time import register_time_sql, timestamp_epoch

COMMANDS = ("status", "search", "history", "context", "person", "topic", "reply", "tasks")
DEFAULT_DB = "~/.wechat-intelligence-hub/radar.db"
MESSAGE_FIELDS = "id, chat, sender, time, content"
ORDER_KEY = "coalesce(message_epoch(time), -9000000000000000000)"
OPPORTUNITY_FIELDS = (
    "id", "chat", "title", "opportunity_type", "stage", "status", "priority",
    "amount", "last_signal_time", "next_action", "next_follow_up",
    "record_type", "confidence",
)


class QueryError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse messages may contain an executable path or user input.
        raise QueryError("invalid_request", "参数无效；请查看 --help 和 Bot 查询说明。")


def parse_args(argv):
    parser = JsonParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--db", default=os.environ.get("WECHAT_HUB_DB", DEFAULT_DB))
    parser.add_argument("--query")
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--chat")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--max-text-chars", type=int, default=2000)
    parser.add_argument("--message-id", type=int)
    parser.add_argument("--radius", type=int, default=2)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--reader", default=os.environ.get("RION_WECHAT_CLI_BIN", "rion-wechat-cli"))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    valid = 1 <= args.limit <= 100 and 1 <= args.max_text_chars <= 4000
    valid = valid and 0 <= args.radius <= 10 and 0 < args.timeout <= 30
    if not valid:
        raise QueryError("invalid_request", "limit 为 1–100，文本为 1–4000 字符，radius 为 0–10，timeout 为 0–30 秒。")
    args.chat = (args.chat or "").strip()
    args.keywords = [value.strip() for value in ([args.query] if args.query is not None else []) + args.keyword]
    if any(not value or len(value) > 256 for value in args.keywords) or len(args.keywords) > 5:
        raise QueryError("invalid_request", "关键词不能为空，每个最多 256 字符，一次最多 5 个。")
    if args.command in ("search", "topic") and not args.keywords:
        raise QueryError("invalid_request", "检索需要 --query 或 --keyword。")
    if args.command == "search" and len(args.keywords) != 1:
        raise QueryError("invalid_request", "search 使用一个关键词；多个关键词请使用 topic。")
    if args.command in ("history", "person", "reply") and not args.chat:
        raise QueryError("invalid_request", "此命令需要 --chat。")
    if len(args.chat) > 256:
        raise QueryError("invalid_request", "聊天对象名称过长。")
    if args.command == "context" and (args.message_id is None or args.message_id <= 0):
        raise QueryError("invalid_request", "context 需要正整数 --message-id。")
    if args.message_id is not None and args.command != "context":
        raise QueryError("invalid_request", "--message-id 仅用于 context。")
    if args.keywords and args.command not in ("search", "topic"):
        raise QueryError("invalid_request", "关键词仅用于 search 或 topic。")
    if args.live and args.command not in ("search", "history", "person", "topic", "reply"):
        raise QueryError("invalid_request", "此命令只查询本地索引，不支持 --live。")
    if args.command == "status" and (args.chat or args.since or args.until):
        raise QueryError("invalid_request", "status 返回索引整体状态，不接受聊天或时间过滤。")
    if args.command == "tasks" and (args.since or args.until):
        raise QueryError("invalid_request", "tasks 返回已保存的任务状态，不支持消息时间过滤。")
    for name in ("since", "until"):
        value = getattr(args, name)
        if value is not None and timestamp_epoch(value) is None:
            raise QueryError("invalid_request", "时间格式无效；请使用 ISO 8601，建议附带时区。")
    if args.since and args.until and timestamp_epoch(args.since) >= timestamp_epoch(args.until):
        raise QueryError("invalid_request", "since 必须早于 until；结束时间不包含在窗口内。")
    return args


def open_index(value):
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise QueryError("database_missing", "本地索引尚不存在；请先初始化索引，或使用支持 --live 的查询。")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma query_only = on")
    register_time_sql(conn)
    conn.create_function("literal_contains", 2, lambda text, needle: int(str(needle).casefold() in str(text or "").casefold()))
    deadline = time.monotonic() + 5
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return conn


def coverage(args, source="local_index"):
    return {
        "source": source, "complete": False, "live_refreshed": source == "wechat_reader",
        "requested_since": args.since, "requested_until": args.until,
        "latest_message_time": None, "last_indexed_at": None, "truncated": False,
        "warnings": ["仅代表本次可读范围；未命中不等于完整微信中不存在。"],
    }


def index_coverage(conn, args):
    result = coverage(args)
    row = conn.execute("select time from messages order by " + ORDER_KEY + " desc, id desc limit 1").fetchone()
    result["latest_message_time"] = row["time"] if row else None
    tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    if "runs" in tables:
        row = conn.execute("select created_at from runs order by coalesce(message_epoch(created_at), 0) desc, id desc limit 1").fetchone()
        result["last_indexed_at"] = row[0] if row else None
    result["warnings"].append("查询不会刷新索引；需要最新消息时使用 --live。")
    result["warnings"].append("索引按保存的聊天名称分组，同名对象可能混合；身份核实请使用 Reader 唯一 ID。")
    return result


def resolve_index_chat(conn, name):
    if conn.execute("select 1 from messages where chat=? limit 1", (name,)).fetchone():
        return name
    rows = conn.execute("select distinct chat from messages where literal_contains(chat, ?) order by chat limit 21", (name,)).fetchall()
    if len(rows) > 1:
        raise QueryError("ambiguous_chat", "存在多个聊天对象，请使用完整名称缩小范围。", candidates=[row[0] for row in rows[:20]], candidates_truncated=len(rows) > 20)
    if not rows:
        raise QueryError("chat_not_found", "此聊天对象尚未进入可读索引。")
    return rows[0][0]


def filters(args, chat=None):
    terms, values = [], []
    if chat:
        terms.append("chat=?")
        values.append(chat)
    for name, operator in (("since", ">="), ("until", "<")):
        value = getattr(args, name)
        if value:
            terms.append("message_epoch(time)" + operator + "?")
            values.append(timestamp_epoch(value))
    return terms, values


def shape_message(row, args, live=False):
    row = dict(row)
    content = str(row.get("content") or row.get("text") or "")
    result = {
        "chat": str(row.get("chat") or ""), "sender": str(row.get("sender") or ""),
        "time": str(row.get("time") or ""), "content": content[:args.max_text_chars],
        "content_truncated": len(content) > args.max_text_chars or bool(row.get("content_truncated")),
        "id_source": "wechat_reader" if live else "local_index",
    }
    if live:
        # Reader local IDs belong to one conversation, not the Hub messages table.
        result["reader_local_id"] = row.get("local_id")
        if row.get("talker"):
            result["reader_chat_id"] = row["talker"]
    else:
        result["id"] = row["id"]
    if "from_me" in row:
        result["from_me"] = bool(row["from_me"])
    return result


def opportunities(conn, args, chat=None):
    fields = {row[1] for row in conn.execute("pragma table_info(opportunities)")}
    if not fields:
        return [], False
    selected = [name for name in OPPORTUNITY_FIELDS if name in fields]
    terms, values = [], []
    if chat and "chat" in fields:
        terms.append("chat=?")
        values.append(chat)
    if args.command == "tasks" and "status" in fields:
        terms.append("status in ('new', 'active', 'waiting', 'paused')")
    where = " where " + " and ".join(terms) if terms else ""
    order = "priority desc, id desc" if "priority" in fields else "id desc"
    rows = conn.execute("select " + ", ".join(selected) + " from opportunities" + where + " order by " + order + " limit ?", values + [args.limit + 1]).fetchall()
    result = []
    for row in rows[:args.limit]:
        item = dict(row)
        clipped = False
        for name, value in list(item.items()):
            if isinstance(value, str) and len(value) > args.max_text_chars:
                item[name] = value[:args.max_text_chars]
                clipped = True
        item["text_truncated"] = clipped
        result.append(item)
    return result, len(rows) > args.limit


def local_query(args):
    conn = open_index(args.db)
    try:
        cov = index_coverage(conn, args)
        if args.command == "status":
            data = {"indexed_messages": conn.execute("select count(*) from messages").fetchone()[0],
                    "indexed_chats": conn.execute("select count(distinct chat) from messages").fetchone()[0]}
            return data, cov
        if args.command == "tasks":
            # Task-only chats may no longer have an indexed message.
            data_rows, cov["truncated"] = opportunities(conn, args, args.chat or None)
            return {"opportunities": data_rows, "pipeline_source": "saved_state"}, cov
        chat = resolve_index_chat(conn, args.chat) if args.chat else None
        terms, values = filters(args, chat)
        if args.command == "context":
            anchor = conn.execute("select " + MESSAGE_FIELDS + ", " + ORDER_KEY + " as sort_time from messages where id=?", (args.message_id,)).fetchone()
            if not anchor:
                raise QueryError("message_not_found", "本地索引中没有此消息 ID。")
            if chat and chat != anchor["chat"]:
                raise QueryError("invalid_request", "消息 ID 与指定聊天对象不一致。")
            chat = anchor["chat"]
            terms, values = filters(args, chat)
            window = " and ".join(terms)
            if not conn.execute("select 1 from messages where " + window + " and id=?", values + [args.message_id]).fetchone():
                raise QueryError("message_not_found", "锚点消息不在请求的时间范围内。")
            key, ident = anchor["sort_time"], anchor["id"]
            before = conn.execute("select " + MESSAGE_FIELDS + " from messages where " + window +
                " and (" + ORDER_KEY + "<? or (" + ORDER_KEY + "=? and id<?)) order by " + ORDER_KEY + " desc, id desc limit ?",
                values + [key, key, ident, args.radius]).fetchall()
            after = conn.execute("select " + MESSAGE_FIELDS + " from messages where " + window +
                " and (" + ORDER_KEY + ">? or (" + ORDER_KEY + "=? and id>?)) order by " + ORDER_KEY + " asc, id asc limit ?",
                values + [key, key, ident, args.radius]).fetchall()
            rows = list(reversed(before)) + [anchor] + list(after)
            if len(rows) > args.limit:
                raise QueryError("invalid_request", "limit 必须容纳完整上下文；请增加 limit 或减少 radius。")
            data = {"chat": chat, "anchor_id": ident}
            cov["warnings"].append("context 仅返回锚点附近的消息。")
        else:
            if args.command in ("search", "topic"):
                terms.append("(" + " or ".join("literal_contains(content, ?)" for _ in args.keywords) + ")")
                values += args.keywords
            where = " where " + " and ".join(terms) if terms else ""
            rows = conn.execute("select " + MESSAGE_FIELDS + " from messages" + where +
                " order by " + ORDER_KEY + " desc, id desc limit ?", values + [args.limit + 1]).fetchall()
            cov["truncated"] = len(rows) > args.limit
            rows = rows[:args.limit]
            if args.command in ("history", "person", "reply"):
                rows = list(reversed(rows))
            data = {"chat": chat} if chat else {}
        data["messages"] = [shape_message(row, args) for row in rows]
        if args.command in ("person", "reply"):
            saved, clipped = opportunities(conn, args, chat)
            data.update(opportunities=saved, pipeline_source="saved_state")
            cov["truncated"] = cov["truncated"] or clipped
            cov["warnings"].append("机会与任务是已保存状态，不代表刚刚重新识别。")
        if args.command == "reply":
            data["reply_mode"] = "draft_only"
        return data, cov
    finally:
        conn.close()


def reader_call(args, command, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise QueryError("reader_timeout", "读取超时；请缩小范围并检查本机 Reader。")
    try:
        completed = subprocess.run(
            [args.reader, *command], capture_output=True, text=True,
            encoding="utf-8", timeout=remaining, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise QueryError("reader_timeout", "读取超时；请缩小范围并检查本机 Reader。") from None
    except (OSError, UnicodeError):
        raise QueryError("reader_failed", "无法调用本机 Reader；请检查安装和已有读取配置。") from None
    if completed.returncode or len(completed.stdout) > 10_000_000:
        raise QueryError("reader_failed", "Reader 读取失败；请在本机诊断，原始日志未返回给 Bot。")
    try:
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise ValueError
        return payload.get("data")
    except (ValueError, TypeError):
        raise QueryError("reader_failed", "Reader 未返回有效 JSON 结果。") from None


def resolve_live_chat(args, deadline):
    result = reader_call(args, ["resolve-chat", args.chat, "--limit", "21"], deadline)
    rows = result.get("candidates", []) if isinstance(result, dict) else result
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise QueryError("reader_failed", "Reader 联系人结果格式无效。")
    exact = [row for row in rows if args.chat in [str(row.get(k) or "") for k in ("username", "display_name", "remark", "nick_name", "alias")]]
    candidates = exact or rows
    if len(candidates) > 1:
        raise QueryError("ambiguous_chat", "存在多个聊天对象，请使用唯一的 Reader username。",
            candidates=[{"name": row.get("display_name", ""), "reader_chat_id": row.get("username", "")} for row in candidates[:20]],
            candidates_truncated=len(candidates) > 20)
    if not candidates:
        raise QueryError("chat_not_found", "Reader 未找到此聊天对象。")
    row = candidates[0]
    if not row.get("username"):
        raise QueryError("reader_failed", "Reader 联系人结果缺少唯一 ID。")
    return str(row["username"]), str(row.get("display_name") or args.chat)


def live_query(args):
    cov = coverage(args, "wechat_reader")
    deadline = time.monotonic() + args.timeout
    username, chat = resolve_live_chat(args, deadline) if args.chat else (None, None)
    collected = []
    for keyword in args.keywords or [None]:
        if args.command in ("search", "topic"):
            command = ["search", keyword, "--limit", str(args.limit + 1),
                       "--max-text-chars", str(args.max_text_chars), "--search-mode", "substring"]
            if username:
                command += ["--in", username]
        else:
            command = ["timeline", username, "--limit", str(args.limit + 1), "--display-order", "desc"]
        if args.since:
            command += ["--after", args.since]
        if args.until:
            command += ["--before", args.until]
        result = reader_call(args, command, deadline)
        rows = result.get("messages", []) if isinstance(result, dict) else result
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise QueryError("reader_failed", "Reader 消息结果格式无效。")
        more = isinstance(result, dict) and (result.get("has_more") or (result.get("query") or {}).get("has_more"))
        cov["truncated"] = cov["truncated"] or bool(more) or len(rows) > args.limit
        collected.extend(rows)
    # Apply the same inclusive/exclusive window even with a compatible backend.
    start, end = timestamp_epoch(args.since), timestamp_epoch(args.until)
    unique = {}
    for row in collected:
        epoch = timestamp_epoch(row.get("time"))
        if (start is not None or end is not None) and epoch is None:
            cov["warnings"].append("部分消息时间无效，已从时间窗口结果排除。")
            continue
        if (start is not None and epoch < start) or (end is not None and epoch >= end):
            continue
        identity = (row.get("talker") or row.get("chat"), row.get("local_id"), row.get("time"), row.get("content") or row.get("text"))
        unique[identity] = row
    rows = sorted(unique.values(), key=lambda row: (timestamp_epoch(row.get("time")) or 0, int(row.get("local_id") or 0)), reverse=True)
    cov["truncated"] = cov["truncated"] or len(rows) > args.limit
    rows = rows[:args.limit]
    cov["latest_message_time"] = rows[0].get("time") if rows else None
    if args.command in ("history", "person", "reply"):
        rows.reverse()
    data = {"messages": [shape_message(row, args, live=True) for row in rows]}
    if chat:
        data.update(chat=chat, reader_chat_id=username)
    if args.command in ("person", "reply"):
        data.update(opportunities=[], pipeline_source="not_loaded")
        cov["warnings"].append("实时查询未读取本地机会状态；需要时另外查询 person 或 tasks。")
    if args.command == "reply":
        data["reply_mode"] = "draft_only"
    cov["warnings"] = list(dict.fromkeys(cov["warnings"]))
    return data, cov


def main(argv=None):
    command = None
    pretty = False
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        command, pretty = args.command, args.pretty
        data, cov = live_query(args) if args.live else local_query(args)
        payload = {"schema_version": 1, "ok": True, "command": command, "data": data, "coverage": cov}
        code = 0
    except QueryError as exc:
        payload = {"schema_version": 1, "ok": False, "command": command,
                   "error": {"code": exc.code, "message": exc.message, **exc.details}}
        code = 2
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
        payload = {"schema_version": 1, "ok": False, "command": command,
                   "error": {"code": "query_failed", "message": "查询未完成；请检查本地索引、Reader 兼容性或缩小范围。"}}
        code = 2
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
