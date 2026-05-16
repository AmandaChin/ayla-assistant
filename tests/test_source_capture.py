from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SourceCaptureNoiseControlTests(unittest.TestCase):
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

    def test_feishu_rules_queue_mentions_and_filter_chitchat(self) -> None:
        with self.server.db_connect() as conn:
            self.server.save_settings_values(
                conn,
                {
                    "capture_important_chats": "search-core",
                    "capture_meego_bound_chats": "meego-project",
                    "capture_keywords": "Ayla\n搜索",
                },
            )

            mention = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "feishu_message",
                    "source_id": "msg_mention",
                    "chat_id": "search-core",
                    "chat_name": "搜索客户端重点群",
                    "title": "排期确认",
                    "content": "@安颖 帮忙确认一下 Meego 需求 FEAT-123 的排期，今天下班前给结论",
                    "mentions": ["安颖"],
                    "author": "同事A",
                },
            )
            chitchat = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "feishu_message",
                    "source_id": "msg_noise",
                    "chat_id": "random-chat",
                    "chat_name": "普通闲聊群",
                    "title": "普通回复",
                    "content": "收到",
                    "author": "同事B",
                },
            )
            conn.commit()

            inbox_count = conn.execute("SELECT COUNT(*) FROM inbox_items").fetchone()[0]
            rows = [self.server.row_to_dict(row) for row in conn.execute("SELECT * FROM source_events ORDER BY source_id").fetchall()]
            health = self.server.source_capture_health(conn)

        self.assertEqual(mention["decision"], "queue_for_batch")
        self.assertGreaterEqual(mention["importance_score"], 0.75)
        self.assertEqual(chitchat["decision"], "index_only")
        self.assertEqual(inbox_count, 1)
        self.assertEqual(health["received"], 2)
        self.assertEqual(health["rule_matched"], 1)
        self.assertEqual(health["model_queued"], 1)
        self.assertEqual(health["filtered_noise"], 1)
        metadata_by_source = {row["source_id"]: row["metadata"] for row in rows}
        self.assertTrue(metadata_by_source["msg_mention"]["capture"]["should_summarize"])
        self.assertFalse(metadata_by_source["msg_noise"]["capture"]["should_model"])
        self.assertFalse(metadata_by_source["msg_noise"]["capture"]["raw_retained"])
        self.assertEqual(metadata_by_source["msg_noise"]["capture"]["raw_ref"], "")
        self.assertTrue(Path(metadata_by_source["msg_mention"]["capture"]["raw_ref"]).is_file())

    def test_meego_bound_chat_is_high_signal_and_source_id_is_deduped(self) -> None:
        with self.server.db_connect() as conn:
            self.server.save_settings_values(
                conn,
                {
                    "capture_important_chats": "",
                    "capture_meego_bound_chats": "meego-project",
                },
            )

            first = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "feishu_message",
                    "source_id": "msg_meego",
                    "chat_id": "meego-project",
                    "chat_name": "Meego 需求群",
                    "title": "节点状态更新",
                    "content": "FEAT-456 节点流转到技术评审，负责人改为安颖，明天确认 case",
                    "author": "同事C",
                },
            )
            duplicate = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "feishu_message",
                    "source_id": "msg_meego",
                    "chat_id": "meego-project",
                    "chat_name": "Meego 需求群",
                    "title": "节点状态更新",
                    "content": "FEAT-456 节点流转到技术评审，负责人改为安颖，明天确认 case",
                    "author": "同事C",
                },
            )
            conn.commit()

            event_count = conn.execute("SELECT COUNT(*) FROM source_events").fetchone()[0]
            inbox_count = conn.execute("SELECT COUNT(*) FROM inbox_items").fetchone()[0]

        self.assertEqual(first["decision"], "queue_for_batch")
        self.assertEqual(duplicate["decision"], "duplicate")
        self.assertEqual(duplicate["source_event_id"], first["source_event_id"])
        self.assertEqual(event_count, 1)
        self.assertEqual(inbox_count, 1)

    def test_daily_model_budget_caps_candidate_queue(self) -> None:
        with self.server.db_connect() as conn:
            self.server.save_settings_values(
                conn,
                {
                    "capture_important_chats": "search-core",
                    "capture_daily_model_call_budget": 1,
                },
            )
            first = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "feishu_message",
                    "source_id": "msg_budget_1",
                    "chat_id": "search-core",
                    "chat_name": "搜索客户端重点群",
                    "title": "排期确认 1",
                    "content": "@安颖 帮忙确认 FEAT-101 的排期和上线风险。",
                    "mentions": ["安颖"],
                    "author": "同事A",
                },
            )
            second = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "feishu_message",
                    "source_id": "msg_budget_2",
                    "chat_id": "search-core",
                    "chat_name": "搜索客户端重点群",
                    "title": "排期确认 2",
                    "content": "@安颖 帮忙确认 FEAT-102 的排期和上线风险。",
                    "mentions": ["安颖"],
                    "author": "同事B",
                },
            )
            conn.commit()

            inbox_count = conn.execute("SELECT COUNT(*) FROM inbox_items").fetchone()[0]
            second_row = self.server.row_to_dict(
                conn.execute("SELECT * FROM source_events WHERE source_id = 'msg_budget_2'").fetchone()
            )
            health = self.server.source_capture_health(conn)

        self.assertEqual(first["decision"], "queue_for_batch")
        self.assertEqual(second["decision"], "index_only")
        self.assertFalse(second["should_model"])
        self.assertEqual(inbox_count, 1)
        self.assertEqual(second_row["metadata"]["capture"]["status"], "budget_capped")
        self.assertEqual(health["model_queued"], 1)
        self.assertEqual(health["by_status"]["budget_capped"], 1)

    def test_browser_allowlist_auto_captures_and_other_domains_need_manual_share(self) -> None:
        with self.server.db_connect() as conn:
            self.server.save_settings_values(
                conn,
                {
                    "browser_capture_allowlist": "meego.larkoffice.com\nbytedance.larkoffice.com",
                },
            )

            allowlisted = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "browser_page",
                    "source_id": "page_meego",
                    "source_url": "https://meego.larkoffice.com/task/123",
                    "title": "Meego 需求详情",
                    "content": "负责人 安颖，当前节点 技术评审，存在阻塞需要确认。",
                },
            )
            passive_other = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "browser_page",
                    "source_id": "page_other",
                    "source_url": "https://example.com/news",
                    "title": "普通网页",
                    "content": "一篇普通新闻。",
                },
            )
            manual_other = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "browser_share",
                    "source_id": "share_other",
                    "source_url": "https://example.com/deep-dive",
                    "title": "主动分享资料",
                    "content": "这篇资料需要整理成工作台资料。",
                },
            )
            conn.commit()

        self.assertEqual(allowlisted["decision"], "queue_for_batch")
        self.assertEqual(passive_other["decision"], "index_only")
        self.assertEqual(manual_other["decision"], "queue_for_batch")

    def test_state_payload_exposes_capture_health_and_source_evidence(self) -> None:
        with self.server.db_connect() as conn:
            self.server.save_settings_values(
                conn,
                {
                    "capture_important_chats": "search-core",
                    "capture_meego_bound_chats": "meego-project",
                    "capture_daily_model_call_budget": 8,
                    "capture_daily_token_budget": 12000,
                },
            )
            signal = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "feishu_message",
                    "source_id": "msg_action",
                    "chat_id": "search-core",
                    "chat_name": "搜索客户端重点群",
                    "title": "上线阻塞",
                    "content": "@安颖 跟进 FEAT-789 今天上线阻塞，待确认负责人。",
                    "mentions": ["安颖"],
                    "author": "同事A",
                },
            )
            noise = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "feishu_message",
                    "source_id": "msg_ack",
                    "chat_id": "random-chat",
                    "chat_name": "普通闲聊群",
                    "title": "收到",
                    "content": "收到",
                    "author": "同事B",
                },
            )
            conn.commit()

            payload = self.server.state_payload(conn)

        health = payload["source_capture_health"]
        self.assertEqual(health["received"], 2)
        self.assertEqual(health["rule_matched"], 1)
        self.assertEqual(health["model_understood"], 1)
        self.assertEqual(health["filtered_noise"], 1)
        self.assertEqual(health["budget"]["daily_model_calls"], 8)
        self.assertEqual(health["budget"]["daily_input_tokens"], 12000)
        evidence_ids = {item["id"] for item in payload["source_capture_evidence"]}
        self.assertIn(signal["source_event_id"], evidence_ids)
        self.assertNotIn(noise["source_event_id"], evidence_ids)

    def test_manual_only_capture_mode_filters_passive_pages_but_allows_manual_share(self) -> None:
        with self.server.db_connect() as conn:
            self.server.save_settings_values(
                conn,
                {
                    "capture_mode": "manual_only",
                    "browser_capture_allowlist": "meego.larkoffice.com",
                },
            )
            passive = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "browser_page",
                    "source_id": "page_meego_passive",
                    "source_url": "https://meego.larkoffice.com/task/789",
                    "title": "Meego 需求详情",
                    "content": "负责人变更，需要确认排期。",
                },
            )
            manual = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "browser_share",
                    "source_id": "share_meego_manual",
                    "source_url": "https://meego.larkoffice.com/task/789",
                    "title": "主动分享 Meego 需求",
                    "content": "负责人变更，需要确认排期。",
                },
            )
            conn.commit()

        self.assertEqual(passive["decision"], "index_only")
        self.assertFalse(passive["should_model"])
        self.assertEqual(manual["decision"], "queue_for_batch")

    def test_webpage_content_is_trimmed_to_capture_budget(self) -> None:
        with self.server.db_connect() as conn:
            self.server.save_settings_values(
                conn,
                {
                    "capture_max_web_content_kb": 1,
                },
            )
            large_content = "需要整理 " + ("Ayla 工作台降噪方案 " * 300)
            result = self.server.ingest_source_event(
                conn,
                {
                    "source_type": "browser_share",
                    "source_id": "share_large",
                    "source_url": "https://example.com/large",
                    "title": "主动分享大网页",
                    "content": large_content,
                },
            )
            row = self.server.row_to_dict(
                conn.execute("SELECT * FROM source_events WHERE id = ?", (result["source_event_id"],)).fetchone()
            )
            raw_ref = row["metadata"]["capture"]["raw_ref"]

        self.assertTrue(row["metadata"]["capture"]["content_truncated"])
        self.assertLessEqual(len(row["content"]), 620)
        self.assertLessEqual(len(Path(raw_ref).read_text(encoding="utf-8")), 1200)
