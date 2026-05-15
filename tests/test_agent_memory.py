from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_server_script(script: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["AYLA_HOME"] = str(Path(tmp) / "AylaData")
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script)],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    return json.loads(result.stdout)


class AgentMemoryTests(unittest.TestCase):
    def test_init_creates_agent_memory_dirs_and_default_knowledge_spaces(self) -> None:
        payload = run_server_script(
            """
            import json
            import server

            server.init_db()
            with server.db_connect() as conn:
                spaces = [
                    row["slug"]
                    for row in conn.execute("SELECT slug FROM knowledge_spaces ORDER BY sort_order").fetchall()
                ]
            paths = {
                "global": (server.VAULT_ROOT / "AgentMemory" / "global").is_dir(),
                "projects": (server.VAULT_ROOT / "AgentMemory" / "projects").is_dir(),
                "tools": (server.VAULT_ROOT / "AgentMemory" / "tools").is_dir(),
                "skills": (server.VAULT_ROOT / "AgentMemory" / "skills").is_dir(),
                "episodes": (server.VAULT_ROOT / "AgentMemory" / "episodes").is_dir(),
            }
            print(json.dumps({"paths": paths, "spaces": spaces}, ensure_ascii=False))
            """
        )

        self.assertTrue(all(payload["paths"].values()))
        self.assertEqual(payload["spaces"], ["work", "coding", "research", "personal", "public"])

    def test_agent_context_uses_agent_memory_not_human_pinned_slots(self) -> None:
        payload = run_server_script(
            """
            import json
            import server

            server.init_db()
            with server.db_connect() as conn:
                item_id = server.create_pinned_slot(
                    conn,
                    {"title": "人看的固定便笺", "content": "这条不应该进入 Agent context", "category": "个人"},
                )["id"]
                memory_id = server.create_agent_memory(
                    conn,
                    {
                        "memory_type": "preference",
                        "scenario": "coding",
                        "scope": "project",
                        "key": "ayla.answer_style",
                        "title": "Ayla 设计偏好",
                        "content": "回答时区分人看的工作台和 Agent 读的持久层。",
                        "source_event_ids": [],
                    },
                )["id"]
                context = server.agent_context_payload(conn, scenario="coding", project="ayla")
            print(json.dumps({"context": context, "memory_id": memory_id, "slot_id": item_id}, ensure_ascii=False))
            """
        )

        context = payload["context"]
        self.assertNotIn("pinned_slots", context)
        self.assertIn("agent_memory", context)
        self.assertEqual(context["agent_memory"]["context_pack"]["scenario"], "coding")
        self.assertEqual(context["agent_memory"]["memories"][0]["id"], payload["memory_id"])
        self.assertNotIn("人看的固定便笺", json.dumps(context, ensure_ascii=False))

    def test_memory_candidate_is_confirmed_into_agent_memory(self) -> None:
        payload = run_server_script(
            """
            import json
            import server

            server.init_db()
            with server.db_connect() as conn:
                result = server.agent_ingest(
                    conn,
                    {
                        "intent": "capture",
                        "raw_input": "用户明确说固定便笺只给人看，AI 记忆要单独本地持久化。",
                        "candidates": [
                            {
                                "type": "memory_candidate",
                                "memory_type": "rule",
                                "scenario": "coding",
                                "scope": "project",
                                "key": "ayla.memory.boundary",
                                "title": "固定便笺和 AI 记忆边界",
                                "content": "固定便笺只给人看；AI 长期记忆必须单独进入 AgentMemory。",
                                "visibility": "private",
                                "risk_level": "low",
                                "requires_confirmation": True,
                            }
                        ],
                    },
                )
                item_id = result["items"][0]["inbox_item_id"]
                materialized = server.confirm_memory(conn, item_id, {})
                memory = conn.execute("SELECT * FROM agent_memories WHERE id = ?", (materialized["memory_id"],)).fetchone()
                context = server.agent_context_payload(conn, scenario="coding", project="ayla")
                confirmation = conn.execute(
                    "SELECT decision FROM confirmations WHERE target_id = ?",
                    (item_id,),
                ).fetchone()
            print(json.dumps({
                "item": result["items"][0],
                "memory": server.row_to_dict(memory),
                "context": context,
                "confirmation": confirmation["decision"],
            }, ensure_ascii=False))
            """
        )

        self.assertEqual(payload["item"]["candidate_type"], "memory_candidate")
        self.assertEqual(payload["item"]["target"], "memory")
        self.assertEqual(payload["memory"]["key"], "ayla.memory.boundary")
        self.assertEqual(payload["memory"]["status"], "active")
        self.assertEqual(payload["confirmation"], "confirmed")
        self.assertIn("固定便笺和 AI 记忆边界", json.dumps(payload["context"]["agent_memory"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
