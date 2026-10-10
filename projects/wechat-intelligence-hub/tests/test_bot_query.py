"""Synthetic, black-box Bot query contracts; no real WeChat data."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
CLI = PROJECT / "wechat_query.py"

class BotQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "intel.sqlite3"
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            create table messages (
                id integer primary key, hash text, chat text, sender text,
                time text, content text, source_file text
            );
            create table runs (
                id integer primary key, run_type text, since_time text,
                until_time text, created_at text, messages_count integer
            );
            create table opportunities (
                id integer primary key, chat text, title text, stage text,
                status text, next_action text, next_follow_up text,
                priority integer, record_type text, confidence text
            );
        """)
        rows = [
            (1, "示例客户甲", "客户", "2026-10-10T09:00:00+08:00", "云渲染预算完成100%，请发报价"),
            (2, "示例客户甲", "我", "2026-10-10T09:01:00+08:00", "明天给你报价"),
            (3, "示例客户甲", "客户", "2026-10-10T09:02:00+08:00", "好的"),
            (4, "示例客户甲", "我", "2026-10-10T10:00:00+08:00", "报价已发，等你反馈"),
            (5, "示例项目群", "渠道", "2026-10-10T10:05:00+08:00", "云渲染与私有化部署方案讨论"),
            (6, "示例客户乙", "客户", "2026-10-10T10:06:00+08:00", "希望了解远控部署"),
            (7, "示例客户甲", "客户", "2026-10-10T10:07:00+08:00", "请确认报价和交付时间"),
        ]
        conn.executemany(
            "insert into messages values (?, '', ?, ?, ?, ?, 'synthetic')", rows)
        conn.execute("insert into runs values (1,'synthetic','2026-10-10T08:00:00+08:00','2026-10-10T11:00:00+08:00','2026-10-10T11:01:00+08:00',7)")
        conn.execute("insert into opportunities values (1,'示例客户甲','示例软件项目','待报价','active','发送报价','2026-10-11',80,'opportunity','confirmed')")
        conn.execute("insert into opportunities values (2,'示例客户乙','待核实项目','新线索','new','核实需求','',20,'candidate','medium')")
        conn.commit()
        conn.close()
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Shanghai")

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args, db=None):
        result = subprocess.run(
            [sys.executable, str(CLI), "--db", str(db or self.db), *args],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=40)
        self.assertTrue(result.stdout.strip(), "Query must return a JSON envelope")
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            self.fail("stdout must contain exactly one JSON document")
        self.assertEqual(result.stderr, "")
        return result, payload

    def test_search_is_literal_and_does_not_create_reports_or_modify_database(self):
        before = self.db.read_bytes()
        files = sorted(p.name for p in self.root.iterdir())
        result, payload = self.call("search", "--query", "100%")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual([m["id"] for m in payload["data"]["messages"]], [1])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), files)
        self.assertFalse(payload["coverage"]["complete"])
        self.assertEqual(payload["coverage"]["source"], "local_index")

    def test_timestamp_offsets_and_exclusive_until_are_honored(self):
        _, p = self.call("history", "--chat", "示例客户甲",
                         "--since", "2026-10-10T01:00:00Z",
                         "--until", "2026-10-10T01:02:00Z")
        self.assertEqual([m["id"] for m in p["data"]["messages"]], [1, 2])

    def test_limit_reports_truncation_and_keeps_latest_messages(self):
        _, p = self.call("search", "--query", "报价", "--limit", "1")
        self.assertEqual([m["id"] for m in p["data"]["messages"]], [7])
        self.assertTrue(p["coverage"]["truncated"])

    def test_context_stays_in_one_chat(self):
        _, p = self.call("context", "--message-id", "2", "--radius", "1")
        self.assertEqual([m["id"] for m in p["data"]["messages"]], [1, 2, 3])

    def test_partial_contact_name_requires_disambiguation(self):
        result, p = self.call("history", "--chat", "示例客户")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p["error"]["code"], "ambiguous_chat")
        self.assertEqual(p["error"]["candidates"], ["示例客户乙", "示例客户甲"])

    def test_person_returns_evidence_and_confirmed_pipeline(self):
        _, p = self.call("person", "--chat", "示例客户甲")
        self.assertEqual(p["data"]["chat"], "示例客户甲")
        self.assertEqual(p["data"]["opportunities"][0]["stage"], "待报价")
        self.assertEqual(len(p["data"]["messages"]), 5)

    def test_reply_returns_context_without_sending(self):
        _, p = self.call("reply", "--chat", "示例客户甲")
        self.assertEqual(p["data"]["messages"][-1]["id"], 7)
        self.assertEqual(p["data"]["reply_mode"], "draft_only")

    def test_topic_supports_multiple_literal_keywords(self):
        _, p = self.call("topic", "--query", "云渲染", "--keyword", "远控")
        self.assertEqual(set(m["id"] for m in p["data"]["messages"]), {1, 5, 6})

    def test_tasks_preserve_candidate_vs_confirmed_distinction(self):
        _, p = self.call("tasks")
        self.assertEqual(p["data"]["opportunities"][0]["record_type"], "opportunity")
        self.assertEqual(p["data"]["opportunities"][1]["record_type"], "candidate")

    def test_status_exposes_index_freshness_without_sensitive_paths(self):
        _, p = self.call("status")
        self.assertEqual(p["data"]["indexed_messages"], 7)
        self.assertEqual(p["coverage"]["latest_message_time"], "2026-10-10T10:07:00+08:00")
        self.assertNotIn(str(self.root), json.dumps(p))
        self.assertEqual(p["coverage"]["last_indexed_at"], "2026-10-10T11:01:00+08:00")

    def test_missing_database_is_not_created(self):
        path = self.root / "absent" / "missing.sqlite3"
        result, p = self.call("status", db=path)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p["error"]["code"], "database_missing")
        self.assertFalse(path.parent.exists())
        self.assertNotIn(str(path), json.dumps(p))

    def test_validation_errors_are_json(self):
        for args in [
            ("search", "--query", "报价", "--limit", "101"),
            ("search", "--query", "报价", "--limit", "zero"),
            ("search", "--query", " "),
            ("history", "--chat", "示例客户甲", "--since", "not-a-date"),
            ("history", "--chat", "示例客户甲", "--since", "2026-10-11", "--until", "2026-10-10"),
            ("unknown-command",),
        ]:
            with self.subTest(args=args):
                result, p = self.call(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(p["error"]["code"], "invalid_request")

    def test_large_content_is_bounded_and_marked(self):
        _, p = self.call("search", "--query", "云渲染", "--max-text-chars", "10")
        self.assertTrue(p["data"]["messages"][0]["content_truncated"])
        self.assertLessEqual(len(p["data"]["messages"][0]["content"]), 10)

    def make_reader(self, body):
        reader = self.root / "fake_reader.py"
        reader.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        reader.chmod(0o755)
        return reader

    def test_live_history_uses_reader_without_writing_index(self):
        reader = self.make_reader(
            "import json,sys\n"
            "if sys.argv[1]=='resolve-chat':\n"
            " print(json.dumps({'ok':True,'data':[{'display_name':'示例客户甲','username':'synthetic-user'}]}))\n"
            "else:\n"
            " print(json.dumps({'ok':True,'data':{'messages':[{'local_id':42,'chat':'示例客户甲','sender':'客户','time':'2026-10-10T12:00:00+08:00','content':'最新询价'}], 'has_more':False}}))\n")
        before = self.db.read_bytes()
        _, p = self.call("history", "--chat", "示例客户甲", "--live", "--reader", str(reader))
        self.assertEqual(p["data"]["messages"][0]["content"], "最新询价")
        self.assertEqual(p["coverage"]["source"], "wechat_reader")
        self.assertTrue(p["coverage"]["live_refreshed"])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse((self.root / "output").exists())

    def test_reader_errors_do_not_expose_raw_error_or_retry(self):
        count = self.root / "calls"
        reader = self.make_reader(
            "from pathlib import Path\nimport sys\n"
            "p=Path('calls'); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
            "print('SECRET_TOKEN=/private/account/path',file=sys.stderr)\n"
            "sys.exit(3)\n")
        result, p = self.call("search", "--query", "报价", "--live", "--reader", str(reader))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p["error"]["code"], "reader_failed")
        self.assertNotIn("SECRET_TOKEN", json.dumps(p))
        self.assertEqual(count.read_text(), "x")

    def test_reader_timeout_is_bounded(self):
        reader = self.make_reader("import time\ntime.sleep(5)\n")
        result, p = self.call("search", "--query", "报价", "--live",
                              "--reader", str(reader), "--timeout", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p["error"]["code"], "reader_timeout")

if __name__ == "__main__":
    unittest.main()
