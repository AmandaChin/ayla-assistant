from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class AylaDailyWorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_home = os.environ.get("AYLA_HOME")
        os.environ["AYLA_HOME"] = str(Path(self.tmp.name) / "AylaData")
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")
        self.server.init_db()

    def tearDown(self) -> None:
        sys.modules.pop("server", None)
        if self.previous_home is None:
            os.environ.pop("AYLA_HOME", None)
        else:
            os.environ["AYLA_HOME"] = self.previous_home
        self.tmp.cleanup()

    def insert_source_event(self, conn, event_id: str, date_key: str, title: str) -> None:
        collected_at = f"{date_key}T10:00:00+08:00"
        metadata = {
            "link_enrichment": {
                "link": {
                    "title": title,
                    "provider": "http-html",
                    "provider_label": "网页",
                },
                "fetch_provider": "http-html",
            }
        }
        conn.execute(
            """
            INSERT INTO source_events (
              id, source_type, source_id, source_url, title, content,
              author, created_at, collected_at, metadata
            )
            VALUES (?, 'web_memo', ?, ?, ?, ?, 'tester', ?, ?, ?)
            """,
            (
                event_id,
                event_id,
                f"https://example.com/{event_id}",
                title,
                f"{title} 内容摘要",
                collected_at,
                collected_at,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )

    def test_state_payload_uses_natural_day_for_today_modules(self) -> None:
        today = self.server.today_key()
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()

        with self.server.db_connect() as conn:
            self.insert_source_event(conn, "event_old", yesterday, "昨天的链接")
            self.insert_source_event(conn, "event_today", today, "今天的链接")
            conn.execute(
                """
                INSERT INTO daily_work_logs (date_key, summary, report, created_at, updated_at)
                VALUES (?, '旧总结', '旧日报', ?, ?)
                """,
                (yesterday, f"{yesterday}T19:00:00+08:00", f"{yesterday}T19:00:00+08:00"),
            )

            payload = self.server.state_payload(conn)

        self.assertEqual(payload["today"], today)
        self.assertEqual(payload["daily_archive"]["date"], today)
        self.assertEqual(payload["today_work_log"]["date"], today)
        self.assertEqual(payload["today_work_log"]["summary"], "")
        self.assertTrue(payload["next_daily_refresh_at"].startswith(tomorrow))
        self.assertEqual([item["id"] for item in payload["link_summaries"]], ["event_today"])
        self.assertEqual([event["id"] for event in payload["daily_archive"]["events"]], ["event_today"])
        report = payload["today_work_log"]["generated_report"]
        self.assertIn("今日备忘归档", report)
        self.assertIn("今日 TODO", report)
        self.assertIn("待跟进 TODO", report)
        self.assertNotIn("每日整理日报", report)
        self.assertNotIn("需要人工调整", report)
        self.assertNotIn("今日输入摘要", report)


if __name__ == "__main__":
    unittest.main()
