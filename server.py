#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import mimetypes
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
VAULT_ROOT = ROOT / "agent-vault"
LOCAL_STATE_ROOT = VAULT_ROOT / "LocalWorkState"
PUBLIC_VAULT_ROOT = VAULT_ROOT / "PublicKnowledgeVault"
RUNTIME_ROOT = VAULT_ROOT / "runtime"
DB_PATH = VAULT_ROOT / "system" / "database.sqlite"
LIBRA_CONNECTOR_SCRIPT = ROOT / "agents" / "libra-connector" / "scripts" / "libra_browser_fetch.mjs"
LIBRA_CACHE_TTL_SECONDS = 300
LIBRA_RECYCLE_THRESHOLD_DAYS = 15
LIBRA_EXPERIMENT_CACHE: dict[str, dict] = {}

DEFAULT_SETTINGS = {
    "state_root_path": str(LOCAL_STATE_ROOT),
    "vault_path": str(PUBLIC_VAULT_ROOT),
    "public_vault_path": str(PUBLIC_VAULT_ROOT),
    "work_library_path": str(LOCAL_STATE_ROOT / "work_records"),
    "summary_frequency": "manual",
    "model_provider": "manual-rules",
    "model_cli": "codex",
    "codex_model": "",
    "claude_model": "",
    "model_cli_timeout_seconds": 45,
    "model_cli_command": "",
    "feishu_enabled": False,
    "feishu_bot_primary": True,
    "lark_cli_path": "lark-cli",
    "lark_sync_days": 7,
    "lark_minutes_auto_parse_align": True,
    "profile_display_name": "未绑定账号",
    "profile_handle": "@ayla.local",
    "profile_avatar": "未",
    "workspace_account_provider": "demo",
    "workspace_account_identity": "",
    "workspace_account_bound_at": "",
    "workspace_account_auth_status": "demo",
    "github_repo": "",
    "agent_api_token": "",
}

PUBLIC_CATEGORY_DIRS = {
    "待整理": "00_Inbox",
    "可公开": "10_Concepts",
    "学习": "20_Resources",
    "方法论": "30_Methods",
    "工具": "40_Tools",
    "待读": "50_ReadLater",
    "项目": "30_Methods",
    "工作": "30_Methods",
    "会议": "20_Resources",
    "人物": "10_Concepts",
    "个人": "00_Inbox",
    "归档": "90_Archive",
}

LOCAL_STATE_CATEGORY_DIRS = {
    "工作": "work_records",
    "学习": "work_records",
    "项目": "work_records",
    "会议": "meeting_actions",
    "实验": "experiment_snapshots",
    "个人": "personal_memos",
    "待整理": "inbox",
    "可公开": "inbox",
    "报告": "reports",
}

CATEGORY_DIRS = PUBLIC_CATEGORY_DIRS

AGENT_ROLES = [
    {
        "name": "Collector Agent",
        "stage": "多入口采集",
        "priority": "P0",
        "status": "本地输入已接入；飞书 Bot 作为主入口待接真实消息",
    },
    {
        "name": "Orchestrator Agent",
        "stage": "Agent 编排层",
        "priority": "P0",
        "status": "统一 AgentRun、候选结果和确认策略",
    },
    {
        "name": "Task Extractor Agent",
        "stage": "TODO 抽取",
        "priority": "P0",
        "status": "从备忘和结构化候选生成可确认 TODO",
    },
    {
        "name": "Review Agent",
        "stage": "人工确认层",
        "priority": "P0",
        "status": "低风险批量确认，高风险进入即时确认队列",
    },
    {
        "name": "Knowledge Curator Agent",
        "stage": "知识与工作沉淀",
        "priority": "P0",
        "status": "公开知识写 PublicKnowledgeVault，内部工作写 LocalWorkState",
    },
    {
        "name": "Work Summary Agent",
        "stage": "阶段总结",
        "priority": "P2",
        "status": "为周报、月报、季度总结聚合素材",
    },
]

CONNECTORS = [
    {"name": "飞书 Bot", "priority": "P1", "mode": "主输入入口", "status": "通过 Agent ingest 接入"},
    {"name": "妙记", "priority": "P1", "mode": "会议 TODO 抽取", "status": "lark-cli 只读同步"},
    {"name": "日历", "priority": "P1", "mode": "计划辅助", "status": "lark-cli 只读同步"},
    {"name": "Libra", "priority": "P2", "mode": "实验状态管理", "status": "浏览器桥接只读列表"},
    {"name": "Meego", "priority": "P2", "mode": "需求节点留盘", "status": "仅保留手动调研 Skill"},
    {"name": "GitHub", "priority": "P3", "mode": "PR 与公开发布", "status": "仅读配置"},
    {"name": "Obsidian", "priority": "P0", "mode": "公开知识 Vault", "status": "本地 Markdown"},
]

PERMISSION_POLICIES = {
    "auto_read": "本地读和外部只读查询可自动执行，只记录范围摘要。",
    "auto_draft": "AI 解析、摘要和标签建议只生成候选，不直接写长期状态。",
    "batch_confirm": "低风险本地写入进入日维度批量确认。",
    "instant_confirm": "TODO、提醒、飞书文档写入和外部动作必须即时确认。",
    "double_confirm": "删除、覆盖、批量外发和公开发布必须二次确认。",
    "forbidden": "凭证、Cookie、密钥和私密正文入日志禁止自动执行。",
}

TASK_WORDS = [
    "todo",
    "待办",
    "需要",
    "记得",
    "跟进",
    "处理",
    "完成",
    "修复",
    "排查",
    "同步",
    "review",
    "fix",
]

RISK_WORDS = ["风险", "阻塞", "blocked", "blocker", "延期", "失败", "异常", "敏感"]
STUDY_WORDS = ["学习", "资料", "课程", "论文", "方法论", "总结", "复盘"]
WORK_WORDS = ["飞书", "会议", "需求", "项目", "接口", "上线", "排期", "评审"]
PERSONAL_WORDS = ["个人", "生活", "提醒", "买", "预约"]
URL_RE = re.compile(r"https?://[^\s<>\"'）)]+", re.I)

DEFAULT_PINNED_SLOTS = [
    ("工作信息", "工作", "固定记录工作职责、常用项目、协作人、常用链接或环境信息。"),
    ("待读书清单", "学习", "- "),
    ("长期目标", "个人", "记录阶段性目标、年度目标和需要持续关注的方向。"),
    ("常用资料", "待整理", "固定保存高频资料、模板、检查清单或入口。"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_key() -> str:
    return datetime.now().date().isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def ensure_dirs() -> None:
    for path in [
        LOCAL_STATE_ROOT / "inbox",
        LOCAL_STATE_ROOT / "tasks",
        LOCAL_STATE_ROOT / "work_records",
        LOCAL_STATE_ROOT / "meeting_actions",
        LOCAL_STATE_ROOT / "experiment_snapshots",
        LOCAL_STATE_ROOT / "reports",
        LOCAL_STATE_ROOT / "audit_log",
        LOCAL_STATE_ROOT / "raw",
        LOCAL_STATE_ROOT / "personal_memos",
        PUBLIC_VAULT_ROOT / "00_Inbox",
        PUBLIC_VAULT_ROOT / "10_Concepts",
        PUBLIC_VAULT_ROOT / "20_Resources",
        PUBLIC_VAULT_ROOT / "30_Methods",
        PUBLIC_VAULT_ROOT / "40_Tools",
        PUBLIC_VAULT_ROOT / "50_ReadLater",
        PUBLIC_VAULT_ROOT / "90_Archive",
        PUBLIC_VAULT_ROOT / "_assets",
        PUBLIC_VAULT_ROOT / "_templates",
        RUNTIME_ROOT / "agent-runner",
        RUNTIME_ROOT / "cache",
        VAULT_ROOT / "private" / "raw_messages",
        VAULT_ROOT / "private" / "work_notes",
        VAULT_ROOT / "private" / "personal_memos",
        VAULT_ROOT / "obsidian" / "work",
        VAULT_ROOT / "obsidian" / "study",
        VAULT_ROOT / "obsidian" / "projects",
        VAULT_ROOT / "obsidian" / "methods",
        VAULT_ROOT / "obsidian" / "meetings",
        VAULT_ROOT / "obsidian" / "people",
        VAULT_ROOT / "obsidian" / "personal",
        VAULT_ROOT / "obsidian" / "inbox",
        VAULT_ROOT / "publishable" / "study_notes",
        VAULT_ROOT / "publishable" / "sanitized_graph",
        VAULT_ROOT / "system" / "sync_logs",
        VAULT_ROOT / "system" / "prompts",
    ]:
        path.mkdir(parents=True, exist_ok=True)


def db_connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_events (
              id TEXT PRIMARY KEY,
              source_type TEXT NOT NULL,
              source_id TEXT,
              source_url TEXT,
              title TEXT,
              content TEXT NOT NULL,
              author TEXT,
              created_at TEXT NOT NULL,
              collected_at TEXT NOT NULL,
              metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS inbox_items (
              id TEXT PRIMARY KEY,
              source_event_id TEXT NOT NULL,
              item_type TEXT NOT NULL,
              title TEXT NOT NULL,
              content TEXT NOT NULL,
              status TEXT NOT NULL,
              suggested_category TEXT,
              confidence REAL NOT NULL DEFAULT 0.5,
              metadata TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(source_event_id) REFERENCES source_events(id)
            );

            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              description TEXT,
              status TEXT NOT NULL,
              priority TEXT NOT NULL,
              due_at TEXT,
              project_id TEXT,
              assignee TEXT,
              source_event_id TEXT,
              source_title TEXT,
              reminder_snoozed_until TEXT NOT NULL DEFAULT '',
              completed_at TEXT,
              completion_note TEXT NOT NULL DEFAULT '',
              memory_note_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(source_event_id) REFERENCES source_events(id)
            );

            CREATE TABLE IF NOT EXISTS notes (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              path TEXT NOT NULL,
              content TEXT NOT NULL,
              type TEXT NOT NULL,
              tags TEXT NOT NULL DEFAULT '[]',
              projects TEXT NOT NULL DEFAULT '[]',
              sensitivity TEXT NOT NULL,
              publishable INTEGER NOT NULL DEFAULT 0,
              source_event_ids TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_logs (
              id TEXT PRIMARY KEY,
              sync_type TEXT NOT NULL,
              target TEXT,
              status TEXT NOT NULL,
              input_ids TEXT NOT NULL DEFAULT '[]',
              output_url TEXT,
              error_message TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pinned_slots (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              content TEXT NOT NULL,
              category TEXT NOT NULL,
              sort_order INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
              id TEXT PRIMARY KEY,
              action TEXT NOT NULL,
              target_type TEXT,
              target_id TEXT,
              detail TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_runs (
              id TEXT PRIMARY KEY,
              intent TEXT NOT NULL,
              source_event_id TEXT,
              input_refs TEXT NOT NULL DEFAULT '[]',
              tool_calls TEXT NOT NULL DEFAULT '[]',
              candidate_output TEXT NOT NULL DEFAULT '{}',
              questions TEXT NOT NULL DEFAULT '[]',
              status TEXT NOT NULL,
              error_message TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(source_event_id) REFERENCES source_events(id)
            );

            CREATE TABLE IF NOT EXISTS confirmations (
              id TEXT PRIMARY KEY,
              risk_level TEXT NOT NULL,
              action_type TEXT NOT NULL,
              target_type TEXT,
              target_id TEXT,
              source_ref TEXT,
              payload TEXT NOT NULL DEFAULT '{}',
              decision TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              decided_at TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_work_logs (
              date_key TEXT PRIMARY KEY,
              summary TEXT NOT NULL DEFAULT '',
              report TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        ensure_columns(
            conn,
            "tasks",
            {
                "reminder_snoozed_until": "TEXT NOT NULL DEFAULT ''",
                "completed_at": "TEXT",
                "completion_note": "TEXT NOT NULL DEFAULT ''",
                "memory_note_id": "TEXT",
            },
        )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                """
                INSERT OR IGNORE INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, json.dumps(value, ensure_ascii=False), now_iso()),
            )
        token_row = conn.execute("SELECT value FROM settings WHERE key = 'agent_api_token'").fetchone()
        current_token = ""
        if token_row:
            try:
                current_token = json.loads(token_row["value"])
            except json.JSONDecodeError:
                current_token = token_row["value"]
        if not current_token:
            conn.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = 'agent_api_token'",
                (json.dumps(uuid.uuid4().hex, ensure_ascii=False), now_iso()),
            )
        slot_count = conn.execute("SELECT COUNT(*) AS count FROM pinned_slots").fetchone()["count"]
        if slot_count == 0:
            now = now_iso()
            for index, (title, category, content) in enumerate(DEFAULT_PINNED_SLOTS):
                conn.execute(
                    """
                    INSERT INTO pinned_slots (id, title, content, category, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (new_id("slot"), title, content, category, index, now, now),
                )


def row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for key in [
        "metadata",
        "tags",
        "projects",
        "source_event_ids",
        "input_ids",
        "detail",
        "input_refs",
        "tool_calls",
        "candidate_output",
        "questions",
        "payload",
    ]:
        if key in data and isinstance(data[key], str):
            try:
                data[key] = json.loads(data[key])
            except json.JSONDecodeError:
                pass
    return data


def get_settings(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = dict(DEFAULT_SETTINGS)
    for row in rows:
        try:
            settings[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            settings[row["key"]] = row["value"]
    return settings


def save_settings_values(conn: sqlite3.Connection, values: dict) -> None:
    now = now_iso()
    for key, value in values.items():
        if key not in DEFAULT_SETTINGS:
            continue
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), now),
        )


def initials_for_name(name: str) -> str:
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not text:
        return "AY"
    words = re.findall(r"[A-Za-z0-9]+", text)
    if words:
        return "".join(word[0] for word in words[:2]).upper()
    compact = re.sub(r"\s+", "", text)
    return compact[:2]


def compact_identity(value: object) -> str:
    if isinstance(value, dict):
        for key in ["email", "user_email", "user_id", "open_id", "openId", "union_id", "unionId", "id"]:
            if value.get(key):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "").strip()


def workspace_account_payload(settings: dict) -> dict:
    provider = str(settings.get("workspace_account_provider") or "demo").strip() or "demo"
    identity = str(settings.get("workspace_account_identity") or "").strip()
    bound = provider != "demo" and bool(identity)
    if bound:
        display_name = str(settings.get("profile_display_name") or "飞书用户").strip() or "飞书用户"
        avatar = str(settings.get("profile_avatar") or "").strip() or initials_for_name(display_name)
    else:
        display_name = "未绑定账号"
        avatar = "未"
    handle = str(settings.get("profile_handle") or "").strip()
    if not handle:
        handle = identity or "@ayla.local"
    return {
        "display_name": display_name,
        "handle": handle,
        "avatar": avatar,
        "provider": provider,
        "identity": identity,
        "bound": bound,
        "bound_at": str(settings.get("workspace_account_bound_at") or ""),
        "auth_status": str(settings.get("workspace_account_auth_status") or "demo"),
    }


def workspace_account_author(settings: dict, fallback: str = "me") -> str:
    account = workspace_account_payload(settings)
    if account["bound"]:
        return account["display_name"] or fallback
    return fallback


def owner_metadata(settings: dict) -> dict:
    account = workspace_account_payload(settings)
    return {
        "display_name": account["display_name"],
        "handle": account["handle"],
        "provider": account["provider"],
        "identity": account["identity"],
        "bound": account["bound"],
        "bound_at": account["bound_at"],
    }


def bind_lark_account_from_status(conn: sqlite3.Connection, status: dict) -> dict:
    auth = status.get("auth") if isinstance(status.get("auth"), dict) else {}
    if not auth.get("ok"):
        message = str(auth.get("message") or status.get("error") or "飞书授权未完成")
        raise ValueError(message)
    identity = compact_identity(auth.get("identity") or auth.get("user_id") or auth.get("open_id"))
    display_name = str(auth.get("user_name") or auth.get("userName") or "").strip() or identity or "飞书用户"
    handle = identity or "lark-cli"
    values = {
        "profile_display_name": display_name,
        "profile_handle": handle,
        "profile_avatar": initials_for_name(display_name),
        "workspace_account_provider": "lark-cli",
        "workspace_account_identity": identity or display_name,
        "workspace_account_bound_at": now_iso(),
        "workspace_account_auth_status": "verified" if auth.get("verified") is not False else "authorized",
        "feishu_enabled": True,
    }
    save_settings_values(conn, values)
    audit(conn, "bind_workspace_account", "settings", "workspace_account", {"provider": "lark-cli"})
    return workspace_account_payload(get_settings(conn))


def audit(
    conn: sqlite3.Connection,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (id, action, target_type, target_id, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            new_id("audit"),
            action,
            target_type,
            target_id,
            json.dumps(detail or {}, ensure_ascii=False),
            now_iso(),
        ),
    )


def classify_text(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in STUDY_WORDS):
        return "学习"
    if any(word in lower for word in WORK_WORDS):
        return "工作"
    if any(word in lower for word in PERSONAL_WORDS):
        return "个人"
    return "待整理"


def extract_tags(text: str) -> list[str]:
    tags = []
    for item in re.findall(r"#([\w\u4e00-\u9fff-]+)", text):
        if item not in tags:
            tags.append(item)
    keyword_tags = [
        ("飞书", "飞书"),
        ("Obsidian", "obsidian"),
        ("图谱", "知识图谱"),
        ("脱敏", "脱敏"),
        ("TODO", "todo"),
        ("待办", "todo"),
    ]
    for needle, tag in keyword_tags:
        if needle.lower() in text.lower() and tag not in tags:
            tags.append(tag)
    return tags[:8]


def extract_project(text: str) -> str:
    patterns = [
        r"项目[:：]\s*([\w\u4e00-\u9fff-]{2,32})",
        r"([\w\u4e00-\u9fff-]{2,32})项目",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def infer_priority(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in ["紧急", "马上", "今天", "p0", "p1", "urgent"]):
        return "high"
    if any(word in lower for word in ["本周", "重要", "p2"]):
        return "medium"
    return "normal"


def infer_due(text: str) -> str:
    if "今天" in text:
        return datetime.now().date().isoformat()
    if "明天" in text:
        return datetime.fromtimestamp(time.time() + 86400).date().isoformat()
    date_match = re.search(r"(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})", text)
    if date_match:
        parts = re.split(r"[-/.]", date_match.group(1))
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    month_day = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if month_day:
        year = datetime.now().year
        return f"{year:04d}-{int(month_day.group(1)):02d}-{int(month_day.group(2)):02d}"
    return ""


def is_task_like(text: str) -> bool:
    lower = text.lower()
    if any(word in lower for word in TASK_WORDS):
        return True
    if re.search(r"(需要|请|帮忙|记得)?确认(一下|下|是否|状态|方案|时间|结果)", text):
        return True
    if infer_due(text) and re.search(r"(去|打|买|预约|提交|完成|处理|跟进|开会|复查|缴费)", text):
        return True
    return False


def is_risk_like(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in RISK_WORDS)


def clean_title(text: str, fallback: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    compact = re.sub(r"^(todo|待办|需要|记得|跟进|处理|完成)[:：\s-]*", "", compact, flags=re.I)
    if not compact:
        compact = fallback
    return compact[:48]


def title_from_content(content: str, fallback: str) -> str:
    for line in content.splitlines():
        line = line.strip(" #:-\t")
        if line:
            return clean_title(line, fallback)
    return fallback


def extract_urls(text: str) -> list[str]:
    urls = []
    for match in URL_RE.findall(text):
        url = match.rstrip("，。；、,.!?")
        if url not in urls:
            urls.append(url)
    return urls


def strip_urls(text: str) -> str:
    return URL_RE.sub("", text).strip(" \n\t，。；、()（）")


def read_charset(headers: object) -> str:
    content_type = ""
    try:
        content_type = headers.get("content-type", "") or headers.get("Content-Type", "")
    except AttributeError:
        return "utf-8"
    match = re.search(r"charset=([\w.-]+)", content_type, flags=re.I)
    return match.group(1) if match else "utf-8"


def compact_text(text: str, limit: int = 420) -> str:
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def parse_html_metadata(raw_html: str, url: str) -> dict:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.I | re.S)
    title = compact_text(title_match.group(1), 96) if title_match else ""
    desc_match = re.search(
        r"<meta[^>]+(?:name|property)=[\"'](?:description|og:description)[\"'][^>]+content=[\"'](.*?)[\"'][^>]*>",
        raw_html,
        flags=re.I | re.S,
    )
    if not desc_match:
        desc_match = re.search(
            r"<meta[^>]+content=[\"'](.*?)[\"'][^>]+(?:name|property)=[\"'](?:description|og:description)[\"'][^>]*>",
            raw_html,
            flags=re.I | re.S,
        )
    description = compact_text(desc_match.group(1), 260) if desc_match else ""
    body = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", raw_html)
    body = re.sub(r"(?is)<br\s*/?>", "\n", body)
    body = re.sub(r"(?is)</p|</div|</li|</h[1-6]", "\n<", body)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    body = compact_text(body, 900)
    return {
        "url": url,
        "title": title or url,
        "description": description,
        "excerpt": body,
    }


def fetch_url_metadata(url: str) -> dict:
    req = urlrequest.Request(
        url,
        headers={
            "User-Agent": "AylaPersonalAgent/0.1 (+local-first-memo-parser)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        },
    )
    with urlrequest.urlopen(req, timeout=8) as response:
        raw = response.read(1024 * 1024)
        charset = read_charset(response.headers)
        text = raw.decode(charset, errors="replace")
        content_type = response.headers.get("content-type", "")
    if "html" in content_type.lower() or "<html" in text[:500].lower():
        return parse_html_metadata(text, url)
    return {
        "url": url,
        "title": url,
        "description": "",
        "excerpt": compact_text(text, 900),
    }


def smart_summary_from_link(user_text: str, link: dict, fetch_error: str = "") -> str:
    user_note = strip_urls(user_text)
    lines = []
    if user_note:
        lines.append(f"用户批注：{user_note}")
        lines.append("")
    lines.append(f"来源链接：{link.get('url', '')}")
    if link.get("title"):
        lines.append(f"网页标题：{link['title']}")
    if link.get("description"):
        lines.append("")
        lines.append("摘要：")
        lines.append(link["description"])
    elif link.get("excerpt"):
        lines.append("")
        lines.append("摘要：")
        lines.append(compact_text(link["excerpt"], 320))
    if link.get("excerpt"):
        lines.append("")
        lines.append("正文片段：")
        lines.append(compact_text(link["excerpt"], 520))
    if fetch_error:
        lines.append("")
        lines.append(f"解析状态：链接已保留，网页内容暂未抓取成功（{fetch_error}）。")
    return "\n".join(lines).strip()


def enrich_link_memo(content: str) -> dict | None:
    urls = extract_urls(content)
    if not urls:
        return None
    url = urls[0]
    fetch_error = ""
    try:
        link = fetch_url_metadata(url)
    except (urlerror.URLError, TimeoutError, ValueError, OSError) as exc:
        fetch_error = exc.__class__.__name__
        link = {"url": url, "title": strip_urls(content) or url, "description": "", "excerpt": ""}
    user_note = strip_urls(content)
    title_base = link.get("title") or user_note or url
    title = clean_title(title_base, "网页资料")
    summary = smart_summary_from_link(content, link, fetch_error)
    combined = "\n".join([content, link.get("title", ""), link.get("description", ""), link.get("excerpt", "")])
    category = classify_text(combined)
    tags = extract_tags(combined)
    if "网页资料" not in tags:
        tags.append("网页资料")
    if user_note and "这个内容不错" in user_note and "待读" not in tags:
        tags.append("待读")
    return {
        "url": url,
        "title": title,
        "content": summary,
        "category": category,
        "tags": tags[:8],
        "fetch_error": fetch_error,
        "link": link,
    }


def split_task_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip(" \t-*0123456789.、")
        if not line:
            continue
        if is_task_like(line):
            lines.append(line)
    if not lines and is_task_like(text):
        lines.append(text.strip())
    return lines[:8]


def infer_auto_target(content: str, category: str, task_like: bool, tags: list[str]) -> tuple[str, str, float]:
    if task_like:
        return "todo", "task_candidate", 0.82
    lower = content.lower()
    note_signals = [
        category in ["工作", "学习", "项目", "方法论", "会议"],
        len(content.strip()) >= 36,
        bool(tags),
        any(word in lower for word in ["资料", "总结", "复盘", "笔记", "知识", "链接", "文档", "方法"]),
    ]
    if any(note_signals):
        return "note", "note_candidate", 0.72
    return "memo", "memo", 0.58


def source_title(conn: sqlite3.Connection, event_id: str | None) -> str:
    if not event_id:
        return ""
    row = conn.execute("SELECT title, source_type FROM source_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return ""
    return row["title"] or row["source_type"]


def create_source_event(
    conn: sqlite3.Connection,
    source_type: str,
    title: str,
    content: str,
    author: str = "me",
    source_url: str = "",
    source_id: str = "",
    metadata: dict | None = None,
) -> str:
    event_id = new_id("src")
    now = now_iso()
    settings = get_settings(conn)
    metadata_payload = dict(metadata or {})
    metadata_payload.setdefault("owner", owner_metadata(settings))
    if author in ["", "me", "lark-cli", "feishu-mock"]:
        author = workspace_account_author(settings, author or "me")
    conn.execute(
        """
        INSERT INTO source_events (
          id, source_type, source_id, source_url, title, content, author,
          created_at, collected_at, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            source_type,
            source_id or event_id,
            source_url,
            title,
            content,
            author,
            now,
            now,
            json.dumps(metadata_payload, ensure_ascii=False),
        ),
    )
    audit(conn, "create_source_event", "source_event", event_id, {"source_type": source_type})
    return event_id


def create_inbox_item(
    conn: sqlite3.Connection,
    event_id: str,
    item_type: str,
    title: str,
    content: str,
    category: str,
    confidence: float,
    metadata: dict | None = None,
    status: str = "待确认",
) -> str:
    item_id = new_id("inbox")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO inbox_items (
          id, source_event_id, item_type, title, content, status,
          suggested_category, confidence, metadata, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            event_id,
            item_type,
            title,
            content,
            status,
            category,
            confidence,
            json.dumps(metadata or {}, ensure_ascii=False),
            now,
            now,
        ),
    )
    audit(conn, "create_inbox_item", "inbox_item", item_id, {"item_type": item_type})
    return item_id


class ModelCliError(RuntimeError):
    pass


class LarkCliError(RuntimeError):
    def __init__(self, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


def clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def truthy(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text in ["1", "true", "yes", "on", "enabled", "启用", "是"]:
        return True
    if text in ["0", "false", "no", "off", "disabled", "停用", "否"]:
        return False
    return default


LARK_REQUIRED_SCOPES = [
    "calendar:calendar.event:read",
    "minutes:minutes.search:read",
]

LARK_MINUTES_TODO_SCOPES = [
    "minutes:minutes:readonly",
    "minutes:minutes.artifacts:read",
    "minutes:minutes.transcript:export",
]

LARK_BINDING_SCOPES = list(dict.fromkeys([*LARK_REQUIRED_SCOPES, *LARK_MINUTES_TODO_SCOPES]))


def lark_cli_binary(settings: dict) -> str:
    configured = str(settings.get("lark_cli_path") or "lark-cli").strip() or "lark-cli"
    expanded = str(Path(configured).expanduser()) if "/" in configured else configured
    if "/" in expanded:
        return expanded
    return shutil.which(expanded) or expanded


def lark_cli_available(command: str) -> bool:
    if "/" in command:
        return Path(command).exists()
    return shutil.which(command) is not None


def run_lark_cli_text(settings: dict, args: list[str], timeout: int = 20) -> str:
    command = lark_cli_binary(settings)
    if not lark_cli_available(command):
        raise LarkCliError("lark-cli command not found", {"command": command})
    try:
        completed = subprocess.run(
            [command, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LarkCliError("lark-cli command timed out", {"command": [command, *args]}) from exc
    if completed.returncode != 0:
        detail = {"command": [command, *args], "stderr": completed.stderr.strip(), "stdout": completed.stdout.strip()}
        raise LarkCliError(lark_error_message(detail), detail)
    return completed.stdout.strip()


def parse_json_output(text: str) -> dict:
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LarkCliError("lark-cli did not return JSON", {"stdout": text[:800]}) from exc
    if isinstance(data, dict):
        return data
    return {"data": data}


def lark_error_message(detail: dict) -> str:
    for field in ["stdout", "stderr"]:
        text = str(detail.get(field) or "").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text.splitlines()[0][:200]
        error_payload = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error_payload, dict):
            return str(error_payload.get("message") or error_payload.get("type") or "lark-cli failed")
        if isinstance(payload, dict) and payload.get("message"):
            return str(payload["message"])
    return "lark-cli failed"


def run_lark_cli_json(settings: dict, args: list[str], timeout: int = 35) -> dict:
    text = run_lark_cli_text(settings, args, timeout=timeout)
    payload = parse_json_output(text)
    if payload.get("ok") is False:
        detail = {"command": [lark_cli_binary(settings), *args], "stdout": text}
        raise LarkCliError(lark_error_message(detail), detail)
    return payload


def compact_error(exc: LarkCliError) -> dict:
    detail = dict(exc.detail)
    for key in ["stdout", "stderr"]:
        if key in detail:
            detail[key] = str(detail[key])[:1200]
    return {"ok": False, "message": str(exc), "detail": detail}


def lark_permission_summary(status: dict) -> list[dict]:
    auth = status.get("auth") if isinstance(status.get("auth"), dict) else {}
    basic = status.get("scope_check") if isinstance(status.get("scope_check"), dict) else {}
    todo = status.get("todo_scope_check") if isinstance(status.get("todo_scope_check"), dict) else {}
    return [
        {
            "key": "lark_cli_auth",
            "label": "飞书 CLI 授权认证",
            "ok": bool(auth.get("ok")),
            "detail": auth.get("user_name") or auth.get("message") or "需要扫码授权",
            "sensitive": True,
        },
        {
            "key": "calendar_minutes_read",
            "label": "日历 / 妙记基础只读权限",
            "ok": bool(basic.get("ok")),
            "detail": "已授权" if basic.get("ok") else ", ".join(basic.get("missing") or LARK_REQUIRED_SCOPES),
            "sensitive": True,
        },
        {
            "key": "minutes_todo_parse",
            "label": "妙记全文与 TODO 抽取权限",
            "ok": bool(todo.get("ok")),
            "detail": "已授权" if todo.get("ok") else ", ".join(todo.get("missing") or LARK_MINUTES_TODO_SCOPES),
            "sensitive": True,
        },
    ]


def first_nested_value(payload: dict, keys: list[str]) -> object:
    candidates = [payload]
    for field in ["data", "payload", "result", "auth", "authorization"]:
        nested = payload.get(field)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        for key in keys:
            if key in candidate and candidate[key] not in [None, ""]:
                return candidate[key]
    return ""


def normalize_lark_auth_session(payload: dict, scopes: list[str]) -> dict:
    verification_url = first_nested_value(
        payload,
        ["verification_uri", "verificationUri", "verification_url", "verificationUrl", "url"],
    )
    complete_url = first_nested_value(
        payload,
        [
            "verification_uri_complete",
            "verificationUriComplete",
            "verification_url_complete",
            "verificationUrlComplete",
            "complete_url",
            "completeUrl",
            "auth_url",
            "authUrl",
        ],
    )
    return {
        "ok": True,
        "verification_url": str(verification_url or complete_url or ""),
        "complete_url": str(complete_url or ""),
        "user_code": str(first_nested_value(payload, ["user_code", "userCode", "code"]) or ""),
        "device_code": str(first_nested_value(payload, ["device_code", "deviceCode"]) or ""),
        "expires_in": first_nested_value(payload, ["expires_in", "expiresIn"]) or "",
        "interval": first_nested_value(payload, ["interval"]) or "",
        "scopes": scopes,
        "started_at": now_iso(),
    }


def start_lark_binding(conn: sqlite3.Connection, payload: dict) -> dict:
    settings = get_settings(conn)
    requested_scopes = payload.get("scopes")
    if isinstance(requested_scopes, list):
        scopes = [str(item).strip() for item in requested_scopes if str(item).strip()]
    else:
        scopes = LARK_BINDING_SCOPES
    scopes = list(dict.fromkeys(scopes or LARK_BINDING_SCOPES))
    login_payload = run_lark_cli_json(
        settings,
        ["auth", "login", "--no-wait", "--json", "--scope", " ".join(scopes)],
        timeout=25,
    )
    session = normalize_lark_auth_session(login_payload, scopes)
    audit(
        conn,
        "start_lark_account_binding",
        "connector",
        "lark-cli",
        {"scope_count": len(scopes), "has_device_code": bool(session.get("device_code"))},
    )
    return session


def complete_lark_binding(conn: sqlite3.Connection, payload: dict) -> dict:
    device_code = str(payload.get("device_code") or payload.get("deviceCode") or "").strip()
    if not device_code:
        raise ValueError("device_code is required")
    settings = get_settings(conn)
    login_payload = run_lark_cli_json(
        settings,
        ["auth", "login", "--json", "--device-code", device_code],
        timeout=90,
    )
    status = lark_connector_status(conn)
    profile = bind_lark_account_from_status(conn, status)
    status["account_binding"] = profile
    return {"ok": True, "login": login_payload, "status": status, "profile": profile}


def claim_lark_binding(conn: sqlite3.Connection) -> dict:
    status = lark_connector_status(conn)
    profile = bind_lark_account_from_status(conn, status)
    status["account_binding"] = profile
    return {"ok": True, "status": status, "profile": profile}


def lark_connector_status(conn: sqlite3.Connection) -> dict:
    settings = get_settings(conn)
    command = lark_cli_binary(settings)
    status = {
        "enabled": truthy(settings.get("feishu_enabled")),
        "command": command,
        "available": lark_cli_available(command),
        "required_scopes": LARK_REQUIRED_SCOPES,
        "todo_extraction_scopes": LARK_MINUTES_TODO_SCOPES,
        "binding_scopes": LARK_BINDING_SCOPES,
        "account_binding": workspace_account_payload(settings),
        "auth": None,
        "scope_check": None,
        "todo_scope_check": None,
        "version": "",
        "last_checked_at": now_iso(),
    }
    if not status["available"]:
        status["error"] = "lark-cli command not found"
        status["permission_summary"] = lark_permission_summary(status)
        return status
    try:
        status["version"] = run_lark_cli_text(settings, ["--version"], timeout=8)
    except LarkCliError as exc:
        status["version_error"] = compact_error(exc)
    try:
        auth = run_lark_cli_json(settings, ["auth", "status", "--verify"], timeout=20)
        status["auth"] = {
            "ok": True,
            "identity": first_nested_value(auth, ["identity"]),
            "token_status": first_nested_value(auth, ["tokenStatus", "token_status"]),
            "user_name": first_nested_value(auth, ["userName", "user_name", "name"]),
            "expires_at": first_nested_value(auth, ["expiresAt", "expires_at"]),
            "verified": first_nested_value(auth, ["verified"]),
        }
    except LarkCliError as exc:
        status["auth"] = compact_error(exc)
    try:
        scope_payload = run_lark_cli_json(
            settings,
            ["auth", "check", "--scope", " ".join(LARK_REQUIRED_SCOPES)],
            timeout=20,
        )
        status["scope_check"] = {
            "ok": bool(first_nested_value(scope_payload, ["ok"])),
            "granted": first_nested_value(scope_payload, ["granted"]) or [],
            "missing": first_nested_value(scope_payload, ["missing"]) or [],
        }
    except LarkCliError as exc:
        status["scope_check"] = compact_error(exc)
    try:
        todo_scope_payload = run_lark_cli_json(
            settings,
            ["auth", "check", "--scope", " ".join(LARK_MINUTES_TODO_SCOPES)],
            timeout=20,
        )
        status["todo_scope_check"] = {
            "ok": bool(first_nested_value(todo_scope_payload, ["ok"])),
            "granted": first_nested_value(todo_scope_payload, ["granted"]) or [],
            "missing": first_nested_value(todo_scope_payload, ["missing"]) or [],
        }
    except LarkCliError as exc:
        status["todo_scope_check"] = compact_error(exc)
    status["permission_summary"] = lark_permission_summary(status)
    return status


def query_value(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    if not values:
        return default
    return str(values[0] or default)


def libra_cache_key(app_id: str, owner_type: str, page: int, page_size: int, limit: int, visible: bool) -> str:
    mode = "visible" if visible else "headless"
    return f"{app_id}:{owner_type}:{page}:{page_size}:{limit}:running:{mode}"


def compact_subprocess_text(value: str, limit: int = 900) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[:limit]


def libra_running(row: dict) -> bool:
    status_code = row.get("status_code")
    try:
        if int(status_code) == 1:
            return True
    except (TypeError, ValueError):
        pass
    return str(row.get("status") or "").strip() == "进行中"


def libra_reversal(row: dict) -> bool:
    if row.get("is_reversal"):
        return True
    try:
        return int(row.get("reversal_type") or 0) in [1, 2]
    except (TypeError, ValueError):
        return False


def normalize_libra_row(row: dict) -> dict:
    return {
        "id": row.get("id") or "",
        "name": row.get("name") or "未命名实验",
        "status": row.get("status") or "",
        "status_code": row.get("status_code"),
        "created_time": row.get("created_time") or row.get("start_time") or "",
        "start_time": row.get("start_time") or "",
        "end_time": row.get("end_time") or "",
        "owners": row.get("owners") if isinstance(row.get("owners"), list) else [],
        "creator": row.get("creator") or "",
        "app_id": row.get("app_id") or "-1",
        "product_name": row.get("product_name") or "",
        "layer_name": row.get("layer_name") or "",
        "is_reversal": bool(row.get("is_reversal")),
        "reversal_type": row.get("reversal_type"),
        "reversal_label": row.get("reversal_label") or ("反转实验" if row.get("is_reversal") else "普通实验"),
        "reversal_key": row.get("reversal_key") or "",
        "url": row.get("url") or "",
    }


def parse_libra_datetime(value: object) -> datetime | None:
    if value in [None, ""]:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp)
        except (OSError, OverflowError, ValueError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in [
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ]:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(raw.replace("/", "-").replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def today_18_deadline() -> str:
    return f"{today_key()}T18:00"


def libra_recycle_source_id(experiment_id: object, date_key: str) -> str:
    return f"libra:experiment:{experiment_id}:recycle:{date_key}"


def libra_recycle_title(row: dict) -> str:
    name = str(row.get("name") or "未命名实验").strip()
    return truncate_text(f"实验回收 TODO：{name}", 96)


def libra_recycle_description(row: dict, created_at: datetime, age_days: int) -> str:
    owners = row.get("owners") if isinstance(row.get("owners"), list) else []
    parts = [
        f"Libra 实验已运行 {age_days} 天，超过 {LIBRA_RECYCLE_THRESHOLD_DAYS} 天关注阈值，请在今日 18:00 前确认是否需要回收、关闭或推进结论。",
        "",
        f"实验 ID：{row.get('id') or ''}",
        f"实验名称：{row.get('name') or ''}",
        f"创建时间：{row.get('created_time') or created_at.strftime('%Y-%m-%d %H:%M')}",
        f"当前状态：{row.get('status') or '进行中'}",
        f"实验标签：{row.get('reversal_label') or '普通实验'}",
    ]
    if owners:
        parts.append(f"负责人：{', '.join(str(owner) for owner in owners[:8])}")
    if row.get("url"):
        parts.append(f"链接：{row['url']}")
    return "\n".join(parts)


def create_libra_recycle_task(conn: sqlite3.Connection, row: dict, created_at: datetime, age_days: int, source_id: str, due_at: str) -> dict:
    title = libra_recycle_title(row)
    description = libra_recycle_description(row, created_at, age_days)
    event_id = create_source_event(
        conn,
        "libra_experiment_recycle",
        title,
        description,
        source_url=str(row.get("url") or ""),
        source_id=source_id,
        metadata={
            "origin": "libra_recycle_guard",
            "experiment_id": row.get("id") or "",
            "experiment_name": row.get("name") or "",
            "created_time": row.get("created_time") or "",
            "age_days": age_days,
            "threshold_days": LIBRA_RECYCLE_THRESHOLD_DAYS,
            "status": row.get("status") or "",
            "status_code": row.get("status_code"),
            "is_reversal": bool(row.get("is_reversal")),
            "reversal_label": row.get("reversal_label") or "",
            "due_at": due_at,
            "visibility": "internal",
            "storage_target": "local_state",
        },
    )
    now = now_iso()
    settings = get_settings(conn)
    task_id = new_id("task")
    conn.execute(
        """
        INSERT INTO tasks (
          id, title, description, status, priority, due_at, project_id,
          assignee, source_event_id, source_title, reminder_snoozed_until,
          completed_at, completion_note, memory_note_id, created_at, updated_at
        )
        VALUES (?, ?, ?, '待办', 'high', ?, 'Libra 实验回收', ?, ?, ?, '', '', '', '', ?, ?)
        """,
        (
            task_id,
            title,
            description,
            due_at,
            workspace_account_author(settings, "me"),
            event_id,
            title,
            now,
            now,
        ),
    )
    audit(
        conn,
        "create_libra_recycle_task",
        "task",
        task_id,
        {
            "source_id": source_id,
            "experiment_id": row.get("id") or "",
            "due_at": due_at,
            "age_days": age_days,
        },
    )
    return {"id": task_id, "source_event_id": event_id, "experiment_id": row.get("id") or ""}


def materialize_libra_recycle_todos(conn: sqlite3.Connection, experiments: list[dict]) -> dict:
    date_key = today_key()
    due_at = today_18_deadline()
    now = datetime.now()
    created: list[dict] = []
    skipped = {
        "not_running": 0,
        "reversal": 0,
        "missing_created_time": 0,
        "below_threshold": 0,
        "duplicate_today": 0,
    }
    eligible = 0
    for row in experiments:
        if not libra_running(row):
            skipped["not_running"] += 1
            continue
        if libra_reversal(row):
            skipped["reversal"] += 1
            continue
        experiment_id = row.get("id")
        created_at = parse_libra_datetime(row.get("created_time") or row.get("start_time"))
        if not experiment_id or not created_at:
            skipped["missing_created_time"] += 1
            continue
        age_delta = now - created_at
        if age_delta <= timedelta(days=LIBRA_RECYCLE_THRESHOLD_DAYS):
            skipped["below_threshold"] += 1
            continue
        eligible += 1
        source_id = libra_recycle_source_id(experiment_id, date_key)
        if existing_source_id(conn, "libra_experiment_recycle", source_id):
            skipped["duplicate_today"] += 1
            continue
        created.append(create_libra_recycle_task(conn, row, created_at, age_delta.days, source_id, due_at))
    return {
        "threshold_days": LIBRA_RECYCLE_THRESHOLD_DAYS,
        "due_at": due_at,
        "eligible": eligible,
        "created": len(created),
        "created_tasks": created,
        "skipped": skipped,
    }


def run_libra_browser_fetch(app_id: str, owner_type: str, page: int, page_size: int, limit: int, visible: bool = False) -> dict:
    if not LIBRA_CONNECTOR_SCRIPT.exists():
        return {
            "ok": False,
            "error": "Libra connector script not found",
            "experiments": [],
            "updated_at": now_iso(),
        }
    command = [
        "node",
        str(LIBRA_CONNECTOR_SCRIPT),
        "--json",
        "--running-only",
        "--app-id",
        app_id,
        "--owner-type",
        owner_type,
        "--page",
        str(page),
        "--page-size",
        str(page_size),
        "--limit",
        str(limit),
    ]
    if visible:
        command.append("--visible")
    started_at = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=100,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "Libra browser bridge timed out",
            "experiments": [],
            "updated_at": now_iso(),
        }
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": compact_subprocess_text(completed.stderr) or compact_subprocess_text(completed.stdout) or "Libra browser bridge failed",
            "experiments": [],
            "updated_at": now_iso(),
        }
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "Libra browser bridge returned invalid JSON",
            "experiments": [],
            "updated_at": now_iso(),
        }
    rows = [normalize_libra_row(row) for row in payload.get("rows", []) if isinstance(row, dict)]
    rows = [row for row in rows if libra_running(row)]
    return {
        "ok": True,
        "route": payload.get("route") or "chrome-profile-browser-bridge",
        "visible": bool(payload.get("visible")),
        "updated_at": now_iso(),
        "duration_ms": int((time.time() - started_at) * 1000),
        "source_count": payload.get("count", len(rows)),
        "count": len(rows),
        "experiments": rows,
    }


def libra_experiments_payload(raw_query: str) -> dict:
    query = parse_qs(raw_query)
    app_id = query_value(query, "app_id", "-1")
    owner_type = query_value(query, "owner_type", "my")
    page = clamp_int(query_value(query, "page", "1"), 1, 1, 100)
    page_size = clamp_int(query_value(query, "page_size", "50"), 50, 1, 100)
    limit = clamp_int(query_value(query, "limit", "50"), 50, 1, 100)
    refresh = truthy(query_value(query, "refresh", "false"))
    visible = truthy(query_value(query, "visible", "false"))
    cache_key = libra_cache_key(app_id, owner_type, page, page_size, limit, visible)
    cached = LIBRA_EXPERIMENT_CACHE.get(cache_key)
    if cached and not refresh and time.time() - float(cached.get("cached_at", 0)) < LIBRA_CACHE_TTL_SECONDS:
        payload = dict(cached["payload"])
        payload["cached"] = True
        return payload
    payload = run_libra_browser_fetch(app_id, owner_type, page, page_size, limit, visible=visible)
    payload.update(
        {
            "cached": False,
            "cache_ttl_seconds": LIBRA_CACHE_TTL_SECONDS,
            "owner_type": owner_type,
            "app_id": app_id,
            "page": page,
            "page_size": page_size,
            "visible": visible,
        }
    )
    if payload.get("ok"):
        with db_connect() as conn:
            payload["recycle_todos"] = materialize_libra_recycle_todos(conn, payload.get("experiments") or [])
            conn.commit()
        LIBRA_EXPERIMENT_CACHE[cache_key] = {"cached_at": time.time(), "payload": payload}
    return payload


def local_date_key(days_offset: int = 0) -> str:
    return (datetime.now().date() + timedelta(days=days_offset)).isoformat()


def normalize_date_arg(value: object, default: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        return text
    return default


def lark_items(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def first_text(mapping: dict, keys: list[str], default: str = "") -> str:
    for key in keys:
        value = mapping.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, dict):
            for nested in ["summary", "name", "display_name", "text", "url", "date_time", "date", "timestamp"]:
                nested_value = value.get(nested)
                if nested_value not in [None, ""]:
                    return str(nested_value)
        return str(value)
    return default


def clean_lark_markup(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def extract_lark_description_field(description: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}[:：]\s*(.*?)(?:\s+(?:所有者|开始时间|时长)[:：]|$)", description)
    return match.group(1).strip() if match else ""


def deterministic_lark_id(prefix: str, item: dict, keys: list[str]) -> str:
    for key in keys:
        value = item.get(key)
        if value not in [None, ""]:
            return f"{prefix}:{value}"
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return f"{prefix}:{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex}"


def format_lark_time(value: object) -> str:
    if isinstance(value, dict):
        for key in ["date_time", "datetime", "display_time", "date", "timestamp"]:
            if value.get(key) not in [None, ""]:
                return format_lark_time(value.get(key))
        return json.dumps(value, ensure_ascii=False)
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{10,13}", text):
        timestamp = int(text)
        if timestamp > 10_000_000_000:
            timestamp = timestamp // 1000
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return text


def format_duration(value: object) -> str:
    try:
        duration = int(float(str(value)))
    except (TypeError, ValueError):
        return str(value or "")
    seconds = duration // 1000 if duration > 24 * 3600 else duration
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分钟"
    return f"{minutes}分钟"


def existing_source_id(conn: sqlite3.Connection, source_type: str, source_id: str) -> str:
    row = conn.execute(
        "SELECT id FROM source_events WHERE source_type = ? AND source_id = ?",
        (source_type, source_id),
    ).fetchone()
    return row["id"] if row else ""


def existing_lark_derived_item(
    conn: sqlite3.Connection,
    source_event_id: str,
    parser_provider: str,
    candidate_type: str,
    title: str = "",
) -> dict | None:
    rows = conn.execute(
        "SELECT id, metadata FROM inbox_items WHERE source_event_id = ?",
        (source_event_id,),
    ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except json.JSONDecodeError:
            continue
        if metadata.get("parser_provider") != parser_provider:
            continue
        if metadata.get("candidate_type") != candidate_type:
            continue
        if title and metadata.get("candidate_fingerprint") != slugify(title):
            continue
        return {"inbox_item_id": row["id"], "metadata": metadata}
    return None


def parse_lark_datetime(value: object) -> datetime | None:
    text = format_lark_time(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", normalized):
        normalized = f"{normalized}T00:00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone()


def lark_calendar_event_is_today_todo(item: dict, start_at: datetime | None, title: str) -> bool:
    if not start_at or start_at.date().isoformat() != today_key():
        return False
    status = str(item.get("self_rsvp_status") or item.get("rsvp_status") or "").strip().lower()
    if status in ["decline", "declined", "reject", "rejected"]:
        return False
    lowered = title.lower()
    skip_words = ["公共假期", "public holiday", "holiday", "skip一次", "取消", "cancelled", "canceled"]
    if any(word in lowered for word in skip_words):
        return False
    return True


def create_lark_calendar_todo_candidate(
    conn: sqlite3.Connection,
    source_event_id: str,
    item: dict,
    title: str,
    content: str,
    source_url: str,
    source_id: str,
) -> dict:
    start_value = item.get("start_time") or item.get("start") or item.get("startTime")
    start_at = parse_lark_datetime(start_value)
    if not lark_calendar_event_is_today_todo(item, start_at, title):
        return {"created": False, "reason": "not_today_todo"}
    existing = existing_lark_derived_item(conn, source_event_id, "lark_calendar_todo", "todo", title)
    if existing:
        return {"created": False, **existing}
    due_at = start_at.isoformat(timespec="minutes") if start_at else ""
    task_content = "\n".join(
        [
            f"今日日程提醒：{title}",
            content,
            "处理建议：按日程时间准时参加；如会议已有后续动作，完成后在 TODO 中补充完成记录。",
        ]
    )
    policy = confirmation_policy("todo", "local_state", "low", False, due_at)
    item_id = create_inbox_item(
        conn,
        source_event_id,
        "task_candidate",
        f"参加：{title}",
        task_content,
        "工作",
        0.82,
        {
            "tags": ["飞书", "日历", "今日", "TODO"],
            "auto_target": "todo",
            "candidate_type": "todo",
            "candidate_fingerprint": slugify(title),
            "storage_target": "local_state",
            "visibility": "internal",
            "risk_level": "low",
            "requires_confirmation": True,
            "confirmation_policy": policy,
            "source_url": source_url,
            "source_refs": [source_id],
            "source_index": [source_id],
            "review_date": today_key(),
            "review_status": "pending",
            "suggested_due_at": due_at,
            "suggested_priority": "normal",
            "parser_provider": "lark_calendar_todo",
            "parser_status": "today_todo",
            "reasoning_hint": "日历来源只用于识别今日 TODO，不沉淀为长期知识。",
        },
        status="待确认",
    )
    confirmation_id = create_confirmation(
        conn,
        "low",
        "todo",
        "inbox_item",
        item_id,
        source_event_id,
        {
            "policy": policy,
            "candidate_type": "todo",
            "storage_target": "local_state",
            "visibility": "internal",
            "title": f"参加：{title}",
            "source_refs": [source_id],
        },
    )
    return {"created": True, "inbox_item_id": item_id, "confirmation_id": confirmation_id}


Q2_OKR_ALIGNMENT_RULES = [
    {
        "objective_id": "O1",
        "objective": "业务支撑",
        "kr": "双列规模 / 图文体裁 / 双列框架 / 双列封面",
        "ka": "双列交互、图文链路、筛选、封面体验和求助帖分发",
        "keywords": ["双列", "图文", "封面", "筛选", "折叠屏", "看后搜", "求助帖", "体裁", "转场", "交互"],
    },
    {
        "objective_id": "O2",
        "objective": "性能体验",
        "kr": "QOE / QOS / 耗时 / 卡顿 / 黑白卡",
        "ka": "loadmore、进内流耗时、详情页耗时、预取、线程调度和图片加载优化",
        "keywords": ["qoe", "qos", "loadmore", "耗时", "卡顿", "黑白卡", "性能", "预取", "线程", "quic", "缓存", "30mlt", "换 query", "换query"],
    },
    {
        "objective_id": "O3",
        "objective": "架构优化",
        "kr": "双列组件化建设 + 框架能力优化",
        "ka": "组件化、KMP、NA 化、新框架、无用实验和无用类清理",
        "keywords": ["架构", "组件化", "kmp", "na化", "na 化", "框架", "基建", "容器", "无用实验", "无用类", "清零"],
    },
    {
        "objective_id": "O4",
        "objective": "团队建设（个人）",
        "kr": "AI Coding 建设 / mentor-mentee 持续推进",
        "ka": "Skill 全团队 KO、通用 skill 仓流水线、workflow 场景落地和 one-one",
        "keywords": ["ai coding", "coding", "skill", "workflow", "ko", "mentor", "mentee", "one-one", "团队", "流水线", "代码评审", "技术方案"],
    },
]


def truncate_text(value: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def align_text_to_q2_okr(title: str, content: str) -> list[dict]:
    text = f"{title}\n{content}".lower()
    links = []
    for rule in Q2_OKR_ALIGNMENT_RULES:
        matched = [keyword for keyword in rule["keywords"] if keyword.lower() in text]
        if not matched:
            continue
        confidence = min(0.94, 0.58 + len(matched) * 0.08)
        links.append(
            {
                "objective_id": rule["objective_id"],
                "objective": rule["objective"],
                "kr": rule["kr"],
                "ka": rule["ka"],
                "matched_keywords": matched,
                "confidence": round(confidence, 2),
                "reason": f"妙记内容命中 {', '.join(matched[:5])}，可作为 {rule['objective_id']} 的工作证据候选。",
            }
        )
    return sorted(links, key=lambda item: item["confidence"], reverse=True)


def existing_parse_align_item(conn: sqlite3.Connection, source_event_id: str) -> dict | None:
    rows = conn.execute(
        "SELECT id, metadata FROM inbox_items WHERE source_event_id = ?",
        (source_event_id,),
    ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except json.JSONDecodeError:
            continue
        if metadata.get("candidate_type") == "okr_alignment":
            return {"inbox_item_id": row["id"], "okr_links": metadata.get("okr_links") or []}
    return None


def minutes_parse_align_content(title: str, content: str, source_url: str, okr_links: list[dict]) -> str:
    signal_lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith(("链接", "妙记："))
    ][:4]
    lines = [f"妙记：{title}", "", "Parse 结果："]
    if signal_lines:
        lines.extend(f"- {line}" for line in signal_lines)
    else:
        lines.append("- 当前妙记只同步到基础元信息，后续可补逐字稿后重新解析。")
    lines.append("")
    lines.append("OKR Align：")
    if okr_links:
        for link in okr_links:
            lines.append(
                f"- {link['objective_id']} {link['objective']} / {link['kr']}："
                f"{link['reason']}（置信度 {int(link['confidence'] * 100)}%）"
            )
    else:
        lines.append("- 未找到高置信 OKR 对齐，需要人工确认目标或补充会议上下文。")
    lines.append("")
    lines.append("建议：确认后把这条妙记作为 OKR 证据和周报素材沉淀到本地工作库。")
    if source_url:
        lines.append(f"来源：{source_url}")
    return "\n".join(lines)


def create_lark_minutes_parse_align(
    conn: sqlite3.Connection,
    source_event_id: str,
    title: str,
    content: str,
    source_url: str,
    source_id: str,
) -> dict:
    existing = existing_parse_align_item(conn, source_event_id)
    if existing:
        return {"created": False, **existing}
    okr_links = align_text_to_q2_okr(title, content)
    summary = (
        f"已自动解析妙记《{title}》，命中 {len(okr_links)} 个 Q2 OKR 对齐候选。"
        if okr_links
        else f"已自动解析妙记《{title}》，暂未命中高置信 Q2 OKR 对齐。"
    )
    candidate_content = minutes_parse_align_content(title, content, source_url, okr_links)
    candidate = {
        "type": "report_material",
        "title": f"OKR 对齐：{title}",
        "content": candidate_content,
        "storage_target": "local_state",
        "visibility": "internal",
        "tags": ["飞书", "妙记", "OKR", "ParseAlign", *[link["objective_id"] for link in okr_links]],
        "source_refs": [source_event_id],
        "risk_level": "low",
        "requires_confirmation": True,
        "confidence": okr_links[0]["confidence"] if okr_links else 0.45,
    }
    run_id = create_agent_run(
        conn,
        "parse_align_minutes",
        source_event_id,
        {
            "summary": summary,
            "candidates": [candidate],
            "okr_links": okr_links,
            "parser": "lark_minutes_auto_parse_align",
        },
        [source_event_id],
        [{"tool": "lark_minutes_auto_parse_align", "source_id": source_id}],
        [],
    )
    policy = confirmation_policy("note", "local_state", "low", False)
    item_id = create_inbox_item(
        conn,
        source_event_id,
        "report_material_candidate",
        candidate["title"],
        candidate_content,
        "会议",
        candidate["confidence"],
        {
            "tags": candidate["tags"],
            "auto_target": "note",
            "candidate_type": "okr_alignment",
            "storage_target": "local_state",
            "visibility": "internal",
            "risk_level": "low",
            "requires_confirmation": True,
            "confirmation_policy": policy,
            "source_url": source_url,
            "source_refs": [source_event_id],
            "source_index": [source_id],
            "okr_links": okr_links,
            "agent_run_id": run_id,
            "review_date": today_key(),
            "review_status": "pending",
            "parser_provider": "lark_minutes_auto_parse_align",
            "parser_status": "parsed_aligned" if okr_links else "needs_manual_alignment",
            "reasoning_hint": truncate_text(summary, 180),
        },
        status="自动分类" if okr_links else "需补充",
    )
    confirmation_id = create_confirmation(
        conn,
        "low",
        "note",
        "inbox_item",
        item_id,
        source_event_id,
        {
            "policy": policy,
            "candidate_type": "okr_alignment",
            "storage_target": "local_state",
            "visibility": "internal",
            "okr_links": okr_links,
            "title": candidate["title"],
        },
    )
    audit(
        conn,
        "lark_minutes_parse_align",
        "source_event",
        source_event_id,
        {"inbox_item_id": item_id, "agent_run_id": run_id, "okr_links": okr_links},
    )
    return {
        "created": True,
        "agent_run_id": run_id,
        "inbox_item_id": item_id,
        "confirmation_id": confirmation_id,
        "okr_links": okr_links,
    }


def create_lark_calendar_candidate(conn: sqlite3.Connection, item: dict) -> dict:
    source_id = deterministic_lark_id(
        "lark_calendar",
        item,
        ["event_id", "id", "uid", "calendar_event_id", "original_event_id"],
    )
    title = first_text(item, ["summary", "title", "subject", "name"], "飞书日程")
    start = format_lark_time(item.get("start_time") or item.get("start") or item.get("startTime"))
    end = format_lark_time(item.get("end_time") or item.get("end") or item.get("endTime"))
    location = first_text(item, ["location", "meeting_room", "room", "place"])
    description = first_text(item, ["description", "desc", "content"])
    source_url = first_text(item, ["url", "share_url", "app_link"])
    lines = [f"日程：{title}"]
    if start or end:
        lines.append(f"时间：{start or '未设置'} - {end or '未设置'}")
    if location:
        lines.append(f"地点：{location}")
    if description:
        lines.append(f"说明：{description}")
    content = "\n".join(lines)
    existing = existing_source_id(conn, "lark_calendar", source_id)
    created = False
    if existing:
        event_id = existing
    else:
        event_id = create_source_event(
            conn,
            "lark_calendar",
            title,
            content,
            author="lark-cli",
            source_url=source_url,
            source_id=source_id,
            metadata={"provider": "lark-cli", "source": "calendar +agenda", "raw": item},
        )
        created = True
    todo = create_lark_calendar_todo_candidate(conn, event_id, item, title, content, source_url, source_id)
    return {
        "source_event_id": event_id,
        "created": created,
        "source_id": source_id,
        "todo": todo,
    }


SELF_REFERENCE_WORDS = ["我", "本人", "用户", "安颖", "Amanda", "owner", "assignee", "负责人"]
TODO_ACTION_WORDS = ["待办", "todo", "action", "跟进", "确认", "处理", "完成", "同步", "review", "评审", "排查", "推进"]


def minute_token_from_item(item: dict, source_id: str = "") -> str:
    token = first_text(item, ["minute_token", "token", "object_token"])
    if token:
        return token
    source_url = first_text(item, ["url", "minute_url", "share_url"])
    meta_data = item.get("meta_data") if isinstance(item.get("meta_data"), dict) else {}
    if not source_url:
        source_url = first_text(meta_data, ["app_link", "url", "link"])
    match = re.search(r"/minutes/([^/?#]+)", source_url)
    if match:
        return match.group(1)
    if source_id.startswith("lark_minutes:"):
        return source_id.split(":", 1)[1]
    return ""


def fetch_lark_minutes_notes_payload(settings: dict, minute_token: str) -> dict:
    if not minute_token:
        return {"ok": False, "error": "missing minute token"}
    try:
        output_dir = str((RUNTIME_ROOT / "lark-notes").relative_to(ROOT))
    except ValueError:
        output_dir = "agent-vault/runtime/lark-notes"
    try:
        payload = run_lark_cli_json(
            settings,
            [
                "vc",
                "+notes",
                "--as",
                "user",
                "--minute-tokens",
                minute_token,
                "--format",
                "json",
                "--output-dir",
                output_dir,
            ],
            timeout=90,
        )
    except LarkCliError as exc:
        return {"ok": False, "error": str(exc), "detail": exc.detail}
    return {"ok": True, "payload": payload}


def collect_lark_todo_nodes(node: object) -> list[object]:
    collected: list[object] = []
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            if lowered in ["todos", "todo", "tasks", "task", "action_items", "actions"]:
                if isinstance(value, list):
                    collected.extend(value)
                elif value not in [None, ""]:
                    collected.append(value)
            else:
                collected.extend(collect_lark_todo_nodes(value))
    elif isinstance(node, list):
        for item in node:
            collected.extend(collect_lark_todo_nodes(item))
    return collected


def lark_todo_node_payload(node: object) -> dict:
    if isinstance(node, str):
        return {"title": truncate_text(node, 80), "content": node, "assignee": "", "due_at": ""}
    if not isinstance(node, dict):
        text = str(node or "").strip()
        return {"title": truncate_text(text, 80), "content": text, "assignee": "", "due_at": ""}
    title = first_text(node, ["title", "summary", "task", "todo", "name", "text", "content", "description"])
    content = first_text(node, ["content", "description", "text", "summary", "task", "todo"], title)
    assignee = first_text(node, ["assignee", "assignees", "owner", "owners", "responsible", "executor", "person", "user"])
    due_at = first_text(node, ["due_at", "deadline", "due_time", "time", "date"])
    if not title:
        title = truncate_text(content, 80)
    return {
        "title": title.strip(),
        "content": content.strip() or title.strip(),
        "assignee": assignee.strip(),
        "due_at": format_lark_time(due_at),
    }


def lark_minutes_todo_is_self_related(todo: dict) -> bool:
    haystack = " ".join([todo.get("title", ""), todo.get("content", ""), todo.get("assignee", "")])
    if any(word.lower() in haystack.lower() for word in SELF_REFERENCE_WORDS):
        return True
    assignee = todo.get("assignee", "").strip()
    if assignee and not any(word.lower() in assignee.lower() for word in SELF_REFERENCE_WORDS):
        return False
    return any(word.lower() in haystack.lower() for word in TODO_ACTION_WORDS)


def create_lark_minutes_todo_candidates(
    conn: sqlite3.Connection,
    source_event_id: str,
    title: str,
    content: str,
    source_url: str,
    source_id: str,
    notes_payload: dict,
) -> dict:
    if not notes_payload.get("ok"):
        return {"created": 0, "skipped": 0, "error": notes_payload.get("error", "notes unavailable")}
    raw_todos = collect_lark_todo_nodes(notes_payload.get("payload"))
    created = 0
    skipped = 0
    results = []
    for node in raw_todos:
        todo = lark_todo_node_payload(node)
        if not todo["title"] or not lark_minutes_todo_is_self_related(todo):
            skipped += 1
            continue
        existing = existing_lark_derived_item(conn, source_event_id, "lark_minutes_todo", "todo", todo["title"])
        if existing:
            skipped += 1
            results.append({"created": False, **existing})
            continue
        due_at = todo.get("due_at", "")
        task_title = todo["title"]
        task_content = "\n".join(
            [
                f"妙记待办：{task_title}",
                f"会议：{title}",
                f"动作：{todo['content']}",
                f"相关人：{todo.get('assignee') or '未明确'}",
                f"截止时间：{due_at or '未明确'}",
            ]
        )
        if source_url:
            task_content += f"\n来源：{source_url}"
        policy = confirmation_policy("todo", "local_state", "low", False, due_at)
        item_id = create_inbox_item(
            conn,
            source_event_id,
            "task_candidate",
            task_title,
            task_content,
            "工作",
            0.86 if todo.get("assignee") else 0.76,
            {
                "tags": ["飞书", "妙记", "TODO"],
                "auto_target": "todo",
                "candidate_type": "todo",
                "candidate_fingerprint": slugify(task_title),
                "storage_target": "local_state",
                "visibility": "internal",
                "risk_level": "low",
                "requires_confirmation": True,
                "confirmation_policy": policy,
                "source_url": source_url,
                "source_refs": [source_id],
                "source_index": [source_id],
                "review_date": today_key(),
                "review_status": "pending",
                "suggested_due_at": due_at,
                "suggested_priority": "normal",
                "parser_provider": "lark_minutes_todo",
                "parser_status": "artifact_todo",
                "reasoning_hint": "从妙记 AI 待办中抽取和本人相关的 TODO。",
            },
            status="待确认",
        )
        confirmation_id = create_confirmation(
            conn,
            "low",
            "todo",
            "inbox_item",
            item_id,
            source_event_id,
            {
                "policy": policy,
                "candidate_type": "todo",
                "storage_target": "local_state",
                "visibility": "internal",
                "title": task_title,
                "source_refs": [source_id],
            },
        )
        created += 1
        results.append({"created": True, "inbox_item_id": item_id, "confirmation_id": confirmation_id})
    return {"created": created, "skipped": skipped, "items": results}


def create_lark_minutes_candidate(conn: sqlite3.Connection, item: dict, auto_parse_align: bool = True) -> dict:
    source_id = deterministic_lark_id("lark_minutes", item, ["minute_token", "token", "object_token", "url"])
    meta_data = item.get("meta_data") if isinstance(item.get("meta_data"), dict) else {}
    display_info = clean_lark_markup(first_text(item, ["display_info"]))
    display_lines = display_info.splitlines()
    title = first_text(item, ["title", "topic", "name"])
    if not title and display_lines:
        title = display_lines[0]
    title = title or "飞书妙记"
    source_url = first_text(item, ["url", "minute_url", "share_url"])
    if not source_url:
        source_url = first_text(meta_data, ["app_link", "url", "link"])
    description = clean_lark_markup(first_text(meta_data, ["description"]))
    duration = format_duration(first_text(item, ["duration"], ""))
    if not duration and description:
        duration = extract_lark_description_field(description, "时长")
    owner = first_text(item, ["owner_id", "owner", "owner_name"])
    if not owner and description:
        owner = extract_lark_description_field(description, "所有者")
    lines = [f"妙记：{title}"]
    if len(display_lines) > 1:
        lines.extend(display_lines[1:])
    else:
        if owner:
            lines.append(f"所有者：{owner}")
        if duration:
            lines.append(f"时长：{duration}")
    if source_url:
        lines.append(f"链接：{source_url}")
    content = "\n".join(lines)
    existing = existing_source_id(conn, "lark_minutes", source_id)
    if existing:
        minute_token = minute_token_from_item(item, source_id)
        notes_payload = fetch_lark_minutes_notes_payload(get_settings(conn), minute_token)
        todos = create_lark_minutes_todo_candidates(conn, existing, title, content, source_url, source_id, notes_payload)
        parse_align = create_lark_minutes_parse_align(conn, existing, title, content, source_url, source_id) if auto_parse_align else None
        return {
            "source_event_id": existing,
            "created": False,
            "source_id": source_id,
            "todos": todos,
            "parse_align": parse_align,
        }
    event_id = create_source_event(
        conn,
        "lark_minutes",
        title,
        content,
        author="lark-cli",
        source_url=source_url,
        source_id=source_id,
        metadata={"provider": "lark-cli", "source": "minutes +search", "raw": item},
    )
    minute_token = minute_token_from_item(item, source_id)
    notes_payload = fetch_lark_minutes_notes_payload(get_settings(conn), minute_token)
    todos = create_lark_minutes_todo_candidates(conn, event_id, title, content, source_url, source_id, notes_payload)
    parse_align = create_lark_minutes_parse_align(conn, event_id, title, content, source_url, source_id) if auto_parse_align else None
    return {
        "source_event_id": event_id,
        "created": True,
        "source_id": source_id,
        "todos": todos,
        "parse_align": parse_align,
    }


def sync_lark_calendar(conn: sqlite3.Connection, settings: dict, start: str, end: str) -> dict:
    payload = run_lark_cli_json(
        settings,
        ["calendar", "+agenda", "--start", start, "--end", end, "--format", "json"],
        timeout=45,
    )
    items = lark_items(payload)
    results = [create_lark_calendar_candidate(conn, item) for item in items]
    return {
        "ok": True,
        "range": {"start": start, "end": end},
        "fetched": len(items),
        "created": sum(1 for item in results if item.get("created")),
        "skipped": sum(1 for item in results if not item.get("created")),
        "todos_created": sum(1 for item in results if (item.get("todo") or {}).get("created")),
        "todos_skipped": sum(1 for item in results if item.get("todo") and not item["todo"].get("created")),
        "items": results,
    }


def fetch_lark_minutes(settings: dict, start: str, end: str, owner_or_participant: str) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    for _ in range(10):
        args = [
            "minutes",
            "+search",
            f"--{owner_or_participant}-ids",
            "me",
            "--start",
            start,
            "--end",
            end,
            "--page-size",
            "30",
            "--format",
            "json",
        ]
        if page_token:
            args.extend(["--page-token", page_token])
        payload = run_lark_cli_json(settings, args, timeout=45)
        items.extend(lark_items(payload))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if not data.get("has_more") or not data.get("page_token"):
            break
        page_token = str(data.get("page_token"))
    return items


def sync_lark_minutes(conn: sqlite3.Connection, settings: dict, start: str, end: str) -> dict:
    auto_parse_align = truthy(settings.get("lark_minutes_auto_parse_align"), True)
    raw_items = [
        *fetch_lark_minutes(settings, start, end, "owner"),
        *fetch_lark_minutes(settings, start, end, "participant"),
    ]
    deduped: dict[str, dict] = {}
    for item in raw_items:
        source_id = deterministic_lark_id("lark_minutes", item, ["minute_token", "token", "object_token", "url"])
        deduped[source_id] = item
    results = [create_lark_minutes_candidate(conn, item, auto_parse_align=auto_parse_align) for item in deduped.values()]
    return {
        "ok": True,
        "range": {"start": start, "end": end},
        "fetched": len(deduped),
        "created": sum(1 for item in results if item.get("created")),
        "skipped": sum(1 for item in results if not item.get("created")),
        "todos_created": sum(int((item.get("todos") or {}).get("created") or 0) for item in results),
        "todos_skipped": sum(int((item.get("todos") or {}).get("skipped") or 0) for item in results),
        "todos_errors": [
            (item.get("todos") or {}).get("error")
            for item in results
            if (item.get("todos") or {}).get("error")
        ],
        "parse_aligned": sum(1 for item in results if (item.get("parse_align") or {}).get("created")),
        "parse_align_skipped": sum(1 for item in results if item.get("parse_align") and not item["parse_align"].get("created")),
        "items": results,
    }


def sync_lark_sources(conn: sqlite3.Connection, payload: dict) -> dict:
    settings = get_settings(conn)
    days = clamp_int(payload.get("days") or settings.get("lark_sync_days"), 7, 1, 31)
    default_start = local_date_key(-(days - 1))
    default_end = local_date_key()
    start = normalize_date_arg(payload.get("start"), default_start)
    end = normalize_date_arg(payload.get("end"), default_end)
    include_calendar = truthy(payload.get("include_calendar"), True)
    include_minutes = truthy(payload.get("include_minutes"), True)
    result = {
        "ok": True,
        "started_at": now_iso(),
        "range": {"start": start, "end": end},
        "calendar": None,
        "minutes": None,
    }
    if include_calendar:
        result["calendar"] = sync_lark_calendar(conn, settings, start, end)
    if include_minutes:
        result["minutes"] = sync_lark_minutes(conn, settings, start, end)
    audit(conn, "lark_sync", "connector", "lark-cli", result)
    return result


def model_cli_argv(settings: dict) -> list[str]:
    custom = str(settings.get("model_cli_command") or "").strip()
    if custom:
        return shlex.split(custom)
    provider = str(settings.get("model_cli") or "codex").strip().lower()
    if provider == "claude":
        argv = [
            "claude",
            "-p",
            "--output-format",
            "text",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
        ]
        model = str(settings.get("claude_model") or "").strip()
        if model:
            argv.extend(["--model", model])
        return argv
    argv = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "-C",
        str(ROOT),
    ]
    model = str(settings.get("codex_model") or "").strip()
    if model:
        argv.extend(["--model", model])
    argv.append("-")
    return argv


def model_cli_status_payload(settings: dict) -> dict:
    try:
        argv = model_cli_argv(settings)
    except ValueError as exc:
        return {"enabled": False, "available": False, "error": str(exc), "command": ""}
    executable = argv[0] if argv else ""
    return {
        "enabled": str(settings.get("model_provider") or "") == "model_cli",
        "provider": str(settings.get("model_cli") or "codex"),
        "available": bool(executable and shutil.which(executable)),
        "executable": executable,
        "command": " ".join(shlex.quote(part) for part in argv),
        "timeout_seconds": clamp_int(settings.get("model_cli_timeout_seconds"), 45, 5, 180),
    }


def compact_agent_context(conn: sqlite3.Connection) -> dict:
    context = agent_context_payload(conn)
    return {
        "today": context.get("today"),
        "categories": context.get("categories", []),
        "storage_targets": context.get("storage_targets", []),
        "visibility": context.get("visibility", []),
        "public_vault_sections": context.get("public_vault_sections", []),
        "local_state_sections": context.get("local_state_sections", []),
        "recent_tasks": context.get("recent_tasks", [])[:10],
        "recent_notes": context.get("recent_notes", [])[:10],
        "pinned_slots": context.get("pinned_slots", [])[:10],
        "rules": context.get("rules", []),
    }


def model_cli_prompt(content: str, partition: str, context: dict) -> str:
    schema = {
        "intent": "capture",
        "summary": "给用户看的简短说明",
        "candidates": [
            {
                "type": "todo|public_note|work_record|report_material|pinned|memo",
                "title": "候选标题",
                "content": "候选内容",
                "storage_target": "local_state|feishu_doc|obsidian_public_vault",
                "visibility": "private|internal|public",
                "tags": ["topic/example"],
                "source_refs": ["manual_memo"],
                "risk_level": "low|medium|high",
                "requires_confirmation": True,
                "confidence": 0.86,
                "due_at": "",
                "priority": "normal",
                "project": "",
            }
        ],
        "questions": [],
        "tool_actions": [],
    }
    return (
        "你是 Ayla 个人工作台的快速整理 Agent。\n"
        "请把用户输入整理成 Ayla candidates JSON。只输出 JSON，不要 Markdown，不要解释。\n\n"
        "分类规则：\n"
        "- TODO、提醒、DDL、需要跟进 -> type=todo, storage_target=local_state。\n"
        "- 公司内部资料、会议纪要、实验状态、需求进展、PR 复盘 -> type=work_record 或 report_material, visibility=internal, storage_target=local_state。\n"
        "- 可公开、可迁移、适合系统学习的资料 -> type=public_note, visibility=public, storage_target=obsidian_public_vault。\n"
        "- 长期稳定个人信息、常用命令、ID、环境信息 -> type=pinned。\n"
        "- 不确定时用 type=memo，并在 questions 中写需要追问的问题。\n"
        "- 有截止时间或外部动作时 requires_confirmation=true。\n"
        "- 不要把内部资料写入 obsidian_public_vault。\n\n"
        f"输出 schema 示例：\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"本地上下文：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        f"用户手动分区：{partition or '自动判断'}\n"
        f"用户输入：\n{content}\n"
    )


def extract_json_payload(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ModelCliError("model_cli returned empty output")
    candidates = [text]
    for match in re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.I | re.S):
        candidates.append(match.strip())
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])
    errors = []
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
            continue
        if isinstance(payload, dict):
            return payload
    raise ModelCliError("model_cli output was not valid JSON: " + "; ".join(errors[:2]))


def run_model_cli(settings: dict, prompt: str) -> dict:
    argv = model_cli_argv(settings)
    if not argv or not shutil.which(argv[0]):
        raise ModelCliError(f"model CLI not found: {argv[0] if argv else 'unknown'}")
    timeout = clamp_int(settings.get("model_cli_timeout_seconds"), 45, 5, 180)
    try:
        result = subprocess.run(
            argv,
            input=prompt,
            text=True,
            capture_output=True,
            cwd=ROOT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ModelCliError(f"model_cli timed out after {timeout}s") from exc
    except OSError as exc:
        raise ModelCliError(str(exc)) from exc
    output = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        detail = compact_text(stderr or output, 480)
        raise ModelCliError(f"model_cli failed with code {result.returncode}: {detail}")
    return extract_json_payload(output)


def normalize_model_cli_payload(payload: dict, content: str, partition: str) -> dict:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ModelCliError("model_cli JSON missing non-empty candidates")
    clean_candidates = []
    for raw in candidates[:8]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip() or title_from_content(str(raw.get("content") or content), "整理候选")
        body = str(raw.get("content") or "").strip() or content
        candidate_type = str(raw.get("type") or "memo").strip().lower()
        visibility = normalize_visibility(raw.get("visibility"))
        storage_target = normalize_storage_target(raw.get("storage_target"), visibility)
        if candidate_type == "public_note":
            visibility = "public"
            storage_target = "obsidian_public_vault"
        if candidate_type in ["todo", "pinned"]:
            storage_target = "local_state"
            if visibility == "public":
                visibility = "internal"
        if candidate_type in ["work_record", "report_material"]:
            storage_target = "local_state" if storage_target == "obsidian_public_vault" else storage_target
            if visibility == "private":
                visibility = "internal"
        if visibility != "public" and storage_target == "obsidian_public_vault":
            storage_target = "local_state"
        clean_candidates.append(
            {
                **raw,
                "type": candidate_type,
                "title": title,
                "content": body,
                "category": str(raw.get("category") or partition or classify_text(body)),
                "storage_target": storage_target,
                "visibility": visibility,
                "tags": coerce_tags(raw.get("tags")),
                "risk_level": normalize_risk_level(raw.get("risk_level")),
                "requires_confirmation": bool(raw.get("requires_confirmation", True)),
                "source_refs": as_string_list(raw.get("source_refs")) or ["manual_memo"],
                "priority": str(raw.get("priority") or "normal"),
                "due_at": str(raw.get("due_at") or ""),
                "confidence": float(raw.get("confidence") or 0.82),
            }
        )
    if not clean_candidates:
        raise ModelCliError("model_cli JSON did not contain usable candidates")
    return {
        "source": "local_web_model_cli",
        "raw_input": content,
        "intent": str(payload.get("intent") or "capture"),
        "summary": str(payload.get("summary") or "").strip() or "已由 model_cli 生成整理候选。",
        "candidates": clean_candidates,
        "questions": as_string_list(payload.get("questions")),
        "tool_actions": as_dict_list(payload.get("tool_actions")),
    }


def memo_to_inbox_via_model_cli(conn: sqlite3.Connection, settings: dict, content: str, partition: str) -> dict:
    prompt = model_cli_prompt(content, partition, compact_agent_context(conn))
    model_payload = run_model_cli(settings, prompt)
    ingest_payload = normalize_model_cli_payload(model_payload, content, partition)
    result = agent_ingest(conn, ingest_payload)
    audit(
        conn,
        "model_cli_memo_ingest",
        "agent_run",
        result.get("agent_run_id"),
        {
            "provider": settings.get("model_cli"),
            "candidates": len(result.get("items") or []),
        },
    )
    result["model_cli"] = {"used": True, "provider": settings.get("model_cli")}
    return result


def memo_to_inbox(conn: sqlite3.Connection, content: str, partition: str = "") -> dict:
    settings = get_settings(conn)
    if str(settings.get("model_provider") or "") == "model_cli":
        try:
            return memo_to_inbox_via_model_cli(conn, settings, content, partition)
        except ModelCliError as exc:
            audit(
                conn,
                "model_cli_fallback",
                "memo",
                None,
                {"error": str(exc), "provider": settings.get("model_cli")},
            )
    link_enrichment = enrich_link_memo(content)
    working_content = link_enrichment["content"] if link_enrichment else content
    category = partition or (link_enrichment["category"] if link_enrichment else classify_text(content))
    tags = link_enrichment["tags"] if link_enrichment else extract_tags(content)
    project = extract_project(content)
    task_like = is_task_like(content)
    risk_like = is_risk_like(content)
    auto_target, item_type, confidence = infer_auto_target(working_content, category, task_like, tags)
    if link_enrichment:
        auto_target, item_type, confidence = "note", "note_candidate", 0.84 if not link_enrichment["fetch_error"] else 0.68
    title = link_enrichment["title"] if link_enrichment else clean_title(content, "新备忘")
    source_url = link_enrichment["url"] if link_enrichment else ""
    source_type = "web_memo" if link_enrichment else "manual_memo"
    risk_level = "medium" if risk_like else "low"
    visibility = "public" if auto_target == "note" and category in ["学习", "方法论", "可公开"] and not risk_like else "private"
    storage_target = "obsidian_public_vault" if visibility == "public" else "local_state"
    policy = confirmation_policy(auto_target, storage_target, risk_level, False, infer_due(content))
    event_id = create_source_event(
        conn,
        source_type,
        title,
        working_content,
        source_url=source_url,
        metadata={
            "tags": tags,
            "project": project,
            "category": category,
            "original_memo": content,
            "link_enrichment": link_enrichment,
        },
    )
    item_id = create_inbox_item(
        conn,
        event_id,
        item_type,
        title,
        working_content,
        category,
        confidence,
        {
            "tags": tags,
            "project": project,
            "is_task": task_like,
            "risk": risk_like,
            "auto_classified": True,
            "auto_target": auto_target,
            "candidate_type": "public_note" if storage_target == "obsidian_public_vault" else "work_record" if auto_target == "note" else auto_target,
            "storage_target": storage_target,
            "visibility": visibility,
            "risk_level": risk_level,
            "requires_confirmation": True,
            "confirmation_policy": policy,
            "source_url": source_url,
            "parser_provider": "local-web-parser",
            "parser_status": "failed" if link_enrichment and link_enrichment["fetch_error"] else "parsed" if link_enrichment else "none",
            "review_date": today_key(),
            "review_status": "pending",
            "suggested_priority": infer_priority(content),
            "suggested_due_at": infer_due(content),
        },
        status="待确认" if policy in ["instant_confirm", "double_confirm"] else "自动分类",
    )
    create_confirmation(
        conn,
        risk_level,
        auto_target,
        "inbox_item",
        item_id,
        event_id,
        {"policy": policy, "storage_target": storage_target, "visibility": visibility},
    )
    return {
        "source_event_id": event_id,
        "inbox_item_id": item_id,
        "auto_target": auto_target,
        "source_url": source_url,
    }


def import_summary(conn: sqlite3.Connection, title: str, content: str) -> dict:
    category = classify_text(content)
    tags = extract_tags(content)
    project = extract_project(content)
    risk_like = is_risk_like(content)
    summary_policy = "instant_confirm" if risk_like else "batch_confirm"
    event_id = create_source_event(
        conn,
        "feishu_summary_mock",
        title or "模拟飞书摘要",
        content,
        author="feishu-mock",
        metadata={"tags": tags, "project": project, "category": category},
    )
    summary_id = create_inbox_item(
        conn,
        event_id,
        "summary",
        title or "模拟飞书摘要",
        content,
        category,
        0.72,
        {
            "tags": tags,
            "project": project,
            "risk": risk_like,
            "auto_target": "note",
            "candidate_type": "work_record",
            "storage_target": "local_state",
            "visibility": "internal",
            "risk_level": "medium" if risk_like else "low",
            "requires_confirmation": True,
            "confirmation_policy": summary_policy,
            "review_date": today_key(),
            "review_status": "pending",
            "source_index": ["mock-summary"],
        },
        status="待确认" if summary_policy == "instant_confirm" else "自动分类",
    )
    create_confirmation(
        conn,
        "medium" if risk_like else "low",
        "note",
        "inbox_item",
        summary_id,
        event_id,
        {"policy": summary_policy, "storage_target": "local_state", "visibility": "internal"},
    )
    task_ids = []
    for line in split_task_lines(content):
        task_id = create_inbox_item(
            conn,
            event_id,
            "task_candidate",
            clean_title(line, "待办候选"),
            line,
            category,
            0.74,
            {
                "tags": tags,
                "project": project,
                "is_task": True,
                "auto_target": "todo",
                "candidate_type": "todo",
                "storage_target": "local_state",
                "visibility": "internal",
                "risk_level": "low",
                "requires_confirmation": True,
                "confirmation_policy": "instant_confirm",
                "suggested_priority": infer_priority(line),
                "suggested_due_at": infer_due(line),
                "source_index": ["mock-summary"],
                "review_date": today_key(),
                "review_status": "pending",
            },
            status="待确认",
        )
        task_ids.append(task_id)
        create_confirmation(
            conn,
            "low",
            "todo",
            "inbox_item",
            task_id,
            event_id,
            {"policy": "instant_confirm", "storage_target": "local_state", "visibility": "internal"},
        )
    return {"source_event_id": event_id, "summary_item_id": summary_id, "task_item_ids": task_ids}


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE).strip("-")
    value = value[:64] or uuid.uuid4().hex[:8]
    return value


def yaml_scalar(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "\n" + "\n".join(f"  - {yaml_scalar(item)}" for item in values)


def note_markdown(
    title: str,
    body: str,
    note_type: str,
    tags: list[str],
    projects: list[str],
    source_label: str,
    source_url: str,
    sensitivity: str,
    publishable: bool,
    visibility: str,
    storage_target: str,
    created_at: str,
    owner: dict | None = None,
) -> str:
    links = []
    for project in projects:
        links.append(f"[[{project}]]")
    body = body.strip()
    if links:
        body = f"关联项目：{' '.join(links)}\n\n{body}"
    owner = owner or {}
    return (
        "---\n"
        f"title: {yaml_scalar(title)}\n"
        f"type: {yaml_scalar(note_type)}\n"
        f"owner: {yaml_scalar(owner.get('display_name') or '')}\n"
        f"owner_handle: {yaml_scalar(owner.get('handle') or '')}\n"
        f"owner_provider: {yaml_scalar(owner.get('provider') or '')}\n"
        f"owner_identity: {yaml_scalar(owner.get('identity') or '')}\n"
        f"tags: {yaml_list(tags)}\n"
        f"projects: {yaml_list(projects)}\n"
        f"source: {yaml_scalar(source_label)}\n"
        f"source_url: {yaml_scalar(source_url)}\n"
        f"created_at: {yaml_scalar(created_at)}\n"
        f"updated_at: {yaml_scalar(created_at)}\n"
        f"sensitivity: {yaml_scalar(sensitivity)}\n"
        f"visibility: {yaml_scalar(visibility)}\n"
        f"storage_target: {yaml_scalar(storage_target)}\n"
        f"publishable: {'true' if publishable else 'false'}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )


def configured_path(settings: dict, key: str, default: Path) -> Path:
    value = Path(str(settings.get(key) or default)).expanduser()
    if not value.is_absolute():
        value = ROOT / value
    return value


def write_note_file(
    settings: dict,
    category: str,
    title: str,
    markdown: str,
    storage_target: str,
    visibility: str,
) -> Path:
    if storage_target == "obsidian_public_vault" and visibility == "public":
        vault = configured_path(settings, "public_vault_path", PUBLIC_VAULT_ROOT)
        section = PUBLIC_CATEGORY_DIRS.get(category, "00_Inbox")
    else:
        vault = configured_path(settings, "state_root_path", LOCAL_STATE_ROOT)
        if storage_target == "feishu_doc":
            section = "reports/feishu_drafts"
        else:
            section = LOCAL_STATE_CATEGORY_DIRS.get(category, "work_records")
    target_dir = vault / section
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = target_dir / f"{stamp}-{slugify(title)}.md"
    suffix = 1
    while path.exists():
        path = target_dir / f"{stamp}-{slugify(title)}-{suffix}.md"
        suffix += 1
    path.write_text(markdown, encoding="utf-8")
    return path


def confirm_task(conn: sqlite3.Connection, item_id: str, payload: dict) -> dict:
    item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise KeyError("inbox item not found")
    item_data = row_to_dict(item)
    metadata = item_data.get("metadata") or {}
    settings = get_settings(conn)
    task_id = new_id("task")
    now = now_iso()
    title = (payload.get("title") or item["title"]).strip()
    due_at = normalize_task_deadline(payload.get("due_at") or metadata.get("suggested_due_at"), default=default_task_deadline())
    assignee = payload.get("assignee") or workspace_account_author(settings, "me")
    conn.execute(
        """
        INSERT INTO tasks (
          id, title, description, status, priority, due_at, project_id,
          assignee, source_event_id, source_title, reminder_snoozed_until,
          completed_at, completion_note, memory_note_id, created_at, updated_at
        )
        VALUES (?, ?, ?, '待办', ?, ?, ?, ?, ?, ?, '', '', '', '', ?, ?)
        """,
        (
            task_id,
            title,
            payload.get("description") or item["content"],
            payload.get("priority") or metadata.get("suggested_priority") or "normal",
            due_at,
            payload.get("project_id") or metadata.get("project") or "",
            assignee,
            item["source_event_id"],
            source_title(conn, item["source_event_id"]),
            now,
            now,
        ),
    )
    metadata["materialized_task_id"] = task_id
    metadata["materialized_at"] = now
    conn.execute(
        "UPDATE inbox_items SET status = '已确认', metadata = ?, updated_at = ? WHERE id = ?",
        (json.dumps(metadata, ensure_ascii=False), now, item_id),
    )
    resolve_pending_confirmations(conn, "inbox_item", item_id, "confirmed")
    audit(conn, "confirm_task", "task", task_id, {"inbox_item_id": item_id})
    return {"task_id": task_id}


def coerce_tags(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def coerce_projects(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def confirm_note(conn: sqlite3.Connection, item_id: str, payload: dict) -> dict:
    item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise KeyError("inbox item not found")
    event = conn.execute("SELECT * FROM source_events WHERE id = ?", (item["source_event_id"],)).fetchone()
    item_data = row_to_dict(item)
    metadata = item_data.get("metadata") or {}
    settings = get_settings(conn)
    tags = coerce_tags(payload.get("tags"))
    tags = tags or metadata.get("tags") or []
    projects = coerce_projects(payload.get("projects"))
    project = metadata.get("project")
    projects = projects or ([project] if project else [])
    category = payload.get("category") or item["suggested_category"] or "待整理"
    title = (payload.get("title") or item["title"]).strip()
    visibility = normalize_visibility(payload.get("visibility") or metadata.get("visibility"))
    storage_target = normalize_storage_target(payload.get("storage_target") or metadata.get("storage_target"), visibility)
    if storage_target == "obsidian_public_vault" and visibility != "public":
        storage_target = "local_state"
    sensitivity = payload.get("sensitivity") or metadata.get("sensitivity") or visibility
    publishable = bool(payload.get("publishable", metadata.get("publishable", False)))
    if storage_target == "obsidian_public_vault" and visibility == "public":
        publishable = True
    created_at = now_iso()
    source_label = event["title"] if event else item["source_event_id"]
    source_url = event["source_url"] if event else ""
    markdown = note_markdown(
        title,
        payload.get("content") or item["content"],
        payload.get("type") or category,
        tags,
        projects,
        source_label,
        source_url,
        sensitivity,
        publishable,
            visibility,
            storage_target,
            created_at,
            owner_metadata(settings),
        )
    path = write_note_file(settings, category, title, markdown, storage_target, visibility)
    note_id = new_id("note")
    conn.execute(
        """
        INSERT INTO notes (
          id, title, path, content, type, tags, projects, sensitivity,
          publishable, source_event_ids, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            title,
            str(path),
            markdown,
            payload.get("type") or category,
            json.dumps(tags, ensure_ascii=False),
            json.dumps(projects, ensure_ascii=False),
            sensitivity,
            1 if publishable else 0,
            json.dumps([item["source_event_id"]], ensure_ascii=False),
            created_at,
            created_at,
        ),
    )
    metadata["materialized_note_id"] = note_id
    metadata["materialized_path"] = str(path)
    metadata["materialized_at"] = created_at
    conn.execute(
        "UPDATE inbox_items SET status = '已确认', metadata = ?, updated_at = ? WHERE id = ?",
        (json.dumps(metadata, ensure_ascii=False), created_at, item_id),
    )
    resolve_pending_confirmations(conn, "inbox_item", item_id, "confirmed")
    audit(conn, "confirm_note", "note", note_id, {"inbox_item_id": item_id, "path": str(path)})
    return {"note_id": note_id, "path": str(path)}


def apply_inbox_override(conn: sqlite3.Connection, item_id: str, payload: dict) -> dict:
    if not payload:
        return {"id": item_id}
    item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise KeyError("inbox item not found")
    item_data = row_to_dict(item)
    metadata = item_data.get("metadata") or {}
    field_map = {
        "title": "title",
        "content": "content",
        "category": "suggested_category",
    }
    updates = []
    values = []
    for source_key, column in field_map.items():
        if source_key in payload:
            updates.append(f"{column} = ?")
            values.append(str(payload.get(source_key) or "").strip())
    if "target" in payload:
        metadata["auto_target"] = str(payload.get("target") or "memo")
    if "tags" in payload:
        metadata["tags"] = coerce_tags(payload.get("tags"))
    if "project_id" in payload:
        metadata["project"] = str(payload.get("project_id") or "").strip()
    if "priority" in payload:
        metadata["suggested_priority"] = str(payload.get("priority") or "normal")
    if "due_at" in payload:
        metadata["suggested_due_at"] = str(payload.get("due_at") or "").strip()
    if "storage_target" in payload:
        metadata["storage_target"] = normalize_storage_target(payload.get("storage_target"), metadata.get("visibility", ""))
    if "visibility" in payload:
        metadata["visibility"] = normalize_visibility(payload.get("visibility"))
        metadata["storage_target"] = normalize_storage_target(metadata.get("storage_target"), metadata["visibility"])
    if "risk_level" in payload:
        metadata["risk_level"] = normalize_risk_level(payload.get("risk_level"))
    updates.append("metadata = ?")
    values.append(json.dumps(metadata, ensure_ascii=False))
    updates.append("updated_at = ?")
    values.append(now_iso())
    values.append(item_id)
    conn.execute(f"UPDATE inbox_items SET {', '.join(updates)} WHERE id = ?", values)
    audit(conn, "update_inbox_item", "inbox_item", item_id, {"fields": sorted(payload.keys())})
    return {"id": item_id}


def update_inbox_status(conn: sqlite3.Connection, item_id: str, status: str) -> dict:
    now = now_iso()
    cur = conn.execute(
        "UPDATE inbox_items SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, item_id),
    )
    if cur.rowcount == 0:
        raise KeyError("inbox item not found")
    decision = "rejected" if status in ["已忽略", "已取消"] else "archived" if status == "已归档" else "needs_info"
    resolve_pending_confirmations(conn, "inbox_item", item_id, decision)
    audit(conn, "update_inbox_status", "inbox_item", item_id, {"status": status})
    return {"id": item_id, "status": status}


def ai_summary_item_target(item: dict) -> str:
    metadata = item.get("metadata") or {}
    target = str(metadata.get("auto_target") or "").strip()
    if target:
        return target
    if item.get("item_type") == "task_candidate":
        return "todo"
    if item.get("item_type") in ["note_candidate", "work_record_candidate", "report_material_candidate"]:
        return "note"
    return "memo"


def should_materialize_ai_summary_item(item: dict) -> bool:
    if item.get("status") not in ["待确认", "自动分类", "未处理"]:
        return False
    if item.get("item_type") not in ["task_candidate", "note_candidate", "work_record_candidate", "report_material_candidate"]:
        return False
    metadata = item.get("metadata") or {}
    review_date = str(metadata.get("review_date") or item.get("created_at") or "")[:10]
    if review_date != today_key():
        return False
    target = ai_summary_item_target(item)
    if target not in ["todo", "note"]:
        return False
    if metadata.get("risk_level") == "high" or metadata.get("confirmation_policy") == "double_confirm":
        return False
    if metadata.get("storage_target") == "feishu_doc":
        return False
    if metadata.get("tool_actions"):
        return False
    return True


def materialize_ai_summary_defaults(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT * FROM inbox_items
        WHERE status IN ('待确认', '自动分类', '未处理')
        ORDER BY created_at ASC
        """
    ).fetchall()
    result = {"todo": 0, "note": 0, "skipped": 0}
    for row in rows:
        item = row_to_dict(row)
        if not should_materialize_ai_summary_item(item):
            result["skipped"] += 1
            continue
        metadata = item.get("metadata") or {}
        target = ai_summary_item_target(item)
        if target == "todo":
            confirm_task(
                conn,
                item["id"],
                {
                    "title": item.get("title"),
                    "description": item.get("content"),
                    "priority": metadata.get("suggested_priority"),
                    "due_at": metadata.get("suggested_due_at"),
                    "project_id": metadata.get("project"),
                },
            )
            result["todo"] += 1
        elif target == "note":
            project = metadata.get("project")
            confirm_note(
                conn,
                item["id"],
                {
                    "title": item.get("title"),
                    "content": item.get("content"),
                    "category": item.get("suggested_category"),
                    "tags": metadata.get("tags"),
                    "projects": [project] if project else [],
                    "storage_target": metadata.get("storage_target"),
                    "visibility": metadata.get("visibility"),
                },
            )
            result["note"] += 1
    if result["todo"] or result["note"]:
        audit(conn, "materialize_ai_summary_defaults", "daily_ai_summary", today_key(), result)
    return result


def default_task_deadline(minutes: int = 60) -> str:
    value = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=minutes)
    return value.strftime("%Y-%m-%dT%H:%M")


def normalize_task_deadline(value: object, *, default: str | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default or ""
    raw = raw.replace(" ", "T")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return f"{raw}T18:00"
    match = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", raw)
    if match:
        return f"{match.group(1)}T{match.group(2)}"
    return default or ""


def create_task(conn: sqlite3.Connection, payload: dict) -> dict:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    description = str(payload.get("description") or "").strip()
    priority = str(payload.get("priority") or "normal").strip() or "normal"
    if priority not in ["high", "medium", "normal", "low"]:
        priority = "normal"
    status = str(payload.get("status") or "待办").strip() or "待办"
    if status not in ["待办", "进行中", "已完成", "已取消", "已归档"]:
        status = "待办"
    due_at = normalize_task_deadline(payload.get("due_at"), default=default_task_deadline())
    project_id = str(payload.get("project_id") or "").strip()
    settings = get_settings(conn)
    assignee = str(payload.get("assignee") or workspace_account_author(settings, "me")).strip() or "me"
    now = now_iso()
    event_id = create_source_event(
        conn,
        "dashboard_task",
        title,
        description or title,
        metadata={
            "origin": "today_workbench",
            "due_at": due_at,
            "priority": priority,
            "project": project_id,
        },
    )
    task_id = new_id("task")
    conn.execute(
        """
        INSERT INTO tasks (
          id, title, description, status, priority, due_at, project_id,
          assignee, source_event_id, source_title, reminder_snoozed_until,
          completed_at, completion_note, memory_note_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, '', '', ?, ?)
        """,
        (
            task_id,
            title,
            description,
            status,
            priority,
            due_at,
            project_id,
            assignee,
            event_id,
            title,
            now if status == "已完成" else "",
            now,
            now,
        ),
    )
    audit(conn, "create_task", "task", task_id, {"origin": "today_workbench", "due_at": due_at})
    return {"id": task_id, "source_event_id": event_id}


def update_task(conn: sqlite3.Connection, task_id: str, payload: dict) -> dict:
    allowed = [
        "title",
        "description",
        "status",
        "priority",
        "due_at",
        "project_id",
        "assignee",
        "reminder_snoozed_until",
    ]
    updates = []
    values = []
    for key in allowed:
        if key in payload:
            if key in ["due_at", "reminder_snoozed_until"]:
                payload[key] = normalize_task_deadline(payload.get(key))
            updates.append(f"{key} = ?")
            values.append(payload[key])
    if "status" in payload and payload.get("status") == "已完成":
        updates.append("completed_at = COALESCE(completed_at, ?)")
        values.append(now_iso())
        updates.append("reminder_snoozed_until = ''")
    if not updates:
        return {"id": task_id}
    updates.append("updated_at = ?")
    values.append(now_iso())
    values.append(task_id)
    cur = conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", values)
    if cur.rowcount == 0:
        raise KeyError("task not found")
    audit(conn, "update_task", "task", task_id, {key: payload.get(key) for key in allowed if key in payload})
    return {"id": task_id}


def complete_task(conn: sqlite3.Connection, task_id: str, payload: dict) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise KeyError("task not found")
    task = row_to_dict(row)
    now = now_iso()
    completion_note = str(payload.get("completion_note") or payload.get("detail") or "").strip()
    memory_note_id = task.get("memory_note_id") or ""
    note_path = ""
    if completion_note:
        settings = get_settings(conn)
        title = f"完成记录：{task['title']}"
        project = str(task.get("project_id") or "").strip()
        projects = [project] if project else []
        source_event_ids = [task["source_event_id"]] if task.get("source_event_id") else []
        event_id = create_source_event(
            conn,
            "task_completion",
            title,
            completion_note,
            metadata={
                "task_id": task_id,
                "task_title": task["title"],
                "completed_at": now,
                "project": project,
            },
        )
        source_event_ids.append(event_id)
        body = "\n\n".join(
            [
                f"任务：{task['title']}",
                f"完成时间：{now}",
                "完成事宜：",
                completion_note,
            ]
        )
        markdown = note_markdown(
            title,
            body,
            "完成记录",
            ["TODO", "完成记录", "长期记忆"],
            projects,
            task.get("source_title") or task["title"],
            "",
            "internal",
            False,
            "internal",
            "local_state",
            now,
            owner_metadata(settings),
        )
        path = write_note_file(settings, "工作", title, markdown, "local_state", "internal")
        memory_note_id = new_id("note")
        conn.execute(
            """
            INSERT INTO notes (
              id, title, path, content, type, tags, projects, sensitivity,
              publishable, source_event_ids, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                memory_note_id,
                title,
                str(path),
                markdown,
                "完成记录",
                json.dumps(["TODO", "完成记录", "长期记忆"], ensure_ascii=False),
                json.dumps(projects, ensure_ascii=False),
                "internal",
                json.dumps(source_event_ids, ensure_ascii=False),
                now,
                now,
            ),
        )
        note_path = str(path)
    conn.execute(
        """
        UPDATE tasks
        SET status = '已完成',
            completed_at = ?,
            completion_note = CASE WHEN ? != '' THEN ? ELSE completion_note END,
            memory_note_id = CASE WHEN ? != '' THEN ? ELSE memory_note_id END,
            reminder_snoozed_until = '',
            updated_at = ?
        WHERE id = ?
        """,
        (now, completion_note, completion_note, memory_note_id, memory_note_id, now, task_id),
    )
    audit(
        conn,
        "complete_task",
        "task",
        task_id,
        {"memory_note_id": memory_note_id, "note_path": note_path, "completion_note_len": len(completion_note)},
    )
    return {"id": task_id, "memory_note_id": memory_note_id, "note_path": note_path}


def delete_note(conn: sqlite3.Connection, note_id: str) -> dict:
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        raise KeyError("note not found")
    settings = get_settings(conn)
    note_path = Path(row["path"]).expanduser()
    allowed_roots = []
    for root_value in [
        settings.get("vault_path"),
        settings.get("public_vault_path"),
        settings.get("state_root_path"),
        str(PUBLIC_VAULT_ROOT),
        str(LOCAL_STATE_ROOT),
        str(VAULT_ROOT / "obsidian"),
    ]:
        if root_value:
            allowed_roots.append(Path(str(root_value)).expanduser().resolve())
    deleted_file = False
    skipped_reason = ""
    try:
        resolved = note_path.resolve()
        allowed = resolved.suffix == ".md" and any(resolved.is_relative_to(root) for root in allowed_roots)
        if allowed and resolved.exists() and resolved.is_file():
            resolved.unlink()
            deleted_file = True
        elif not allowed:
            skipped_reason = "path outside configured vault"
    except FileNotFoundError:
        skipped_reason = "file already missing"
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    audit(
        conn,
        "delete_note",
        "note",
        note_id,
        {"path": row["path"], "deleted_file": deleted_file, "skipped_reason": skipped_reason},
    )
    return {"id": note_id, "deleted_file": deleted_file}


def delete_task(conn: sqlite3.Connection, task_id: str) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise KeyError("task not found")
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    audit(
        conn,
        "delete_task",
        "task",
        task_id,
        {
            "title": row["title"],
            "source_event_id": row["source_event_id"],
            "memory_note_id": row["memory_note_id"],
        },
    )
    return {"id": task_id, "deleted": True}


def update_settings(conn: sqlite3.Connection, payload: dict) -> dict:
    now = now_iso()
    for key in DEFAULT_SETTINGS:
        if key in payload:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, json.dumps(payload[key], ensure_ascii=False), now),
            )
    audit(conn, "update_settings", "settings", "global", {key: payload.get(key) for key in DEFAULT_SETTINGS if key in payload})
    return get_settings(conn)


def rotate_agent_token(conn: sqlite3.Connection) -> dict:
    token = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES ('agent_api_token', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (json.dumps(token, ensure_ascii=False), now_iso()),
    )
    audit(conn, "rotate_agent_token", "settings", "agent_api_token", {})
    return {"agent_api_token": token}


def get_pinned_slots(conn: sqlite3.Connection) -> list[dict]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT * FROM pinned_slots ORDER BY sort_order ASC, updated_at DESC"
        ).fetchall()
    ]


def create_pinned_slot(conn: sqlite3.Connection, payload: dict) -> dict:
    content = str(payload.get("content", "")).strip()
    title = str(payload.get("title", "")).strip() or title_from_content(content, "新的固定便笺")
    category = str(payload.get("category", "")).strip() or "待整理"
    current = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS max_order FROM pinned_slots").fetchone()
    now = now_iso()
    slot_id = new_id("slot")
    conn.execute(
        """
        INSERT INTO pinned_slots (id, title, content, category, sort_order, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (slot_id, title, content, category, int(current["max_order"]) + 1, now, now),
    )
    audit(conn, "create_pinned_slot", "pinned_slot", slot_id, {"title": title})
    return {"id": slot_id}


def update_pinned_slot(conn: sqlite3.Connection, slot_id: str, payload: dict) -> dict:
    allowed = ["title", "content", "category", "sort_order"]
    updates = []
    values = []
    for key in allowed:
        if key in payload:
            updates.append(f"{key} = ?")
            values.append(payload[key])
    if not updates:
        return {"id": slot_id}
    updates.append("updated_at = ?")
    values.append(now_iso())
    values.append(slot_id)
    cur = conn.execute(f"UPDATE pinned_slots SET {', '.join(updates)} WHERE id = ?", values)
    if cur.rowcount == 0:
        raise KeyError("pinned slot not found")
    audit(conn, "update_pinned_slot", "pinned_slot", slot_id, {key: payload.get(key) for key in allowed if key in payload})
    return {"id": slot_id}


def delete_pinned_slot(conn: sqlite3.Connection, slot_id: str) -> dict:
    cur = conn.execute("DELETE FROM pinned_slots WHERE id = ?", (slot_id,))
    if cur.rowcount == 0:
        raise KeyError("pinned slot not found")
    audit(conn, "delete_pinned_slot", "pinned_slot", slot_id, {})
    return {"id": slot_id}


def normalize_storage_target(value: object, visibility: str = "") -> str:
    target = str(value or "").strip().lower()
    aliases = {
        "obsidian": "obsidian_public_vault",
        "public_vault": "obsidian_public_vault",
        "publicknowledgevault": "obsidian_public_vault",
        "public_knowledge": "obsidian_public_vault",
        "work": "local_state",
        "local": "local_state",
        "local_work_state": "local_state",
        "feishu": "feishu_doc",
        "lark_doc": "feishu_doc",
    }
    target = aliases.get(target, target)
    if target in ["local_state", "feishu_doc", "obsidian_public_vault"]:
        return target
    return "obsidian_public_vault" if visibility == "public" else "local_state"


def normalize_visibility(value: object) -> str:
    visibility = str(value or "").strip().lower()
    aliases = {"corp": "internal", "company": "internal", "private_work": "internal"}
    visibility = aliases.get(visibility, visibility)
    return visibility if visibility in ["public", "private", "internal"] else "private"


def normalize_risk_level(value: object) -> str:
    risk = str(value or "").strip().lower()
    aliases = {"p0": "high", "p1": "high", "normal": "low", "safe": "low"}
    risk = aliases.get(risk, risk)
    return risk if risk in ["low", "medium", "high"] else "low"


def target_for_candidate(candidate_type: str, fallback: str = "") -> str:
    value = str(candidate_type or "").strip().lower()
    mapping = {
        "todo": "todo",
        "task": "todo",
        "public_note": "note",
        "knowledge": "note",
        "work_record": "note",
        "report_material": "note",
        "pinned": "pinned",
        "memo": "memo",
    }
    return mapping.get(value) or normalize_agent_intent(fallback)


def item_type_for_candidate(candidate_type: str, target: str) -> str:
    if candidate_type == "work_record":
        return "work_record_candidate"
    if candidate_type == "report_material":
        return "report_material_candidate"
    return item_type_for_target(target)


def confirmation_policy(target: str, storage_target: str, risk_level: str, has_tool_actions: bool, due_at: str = "") -> str:
    if risk_level == "high":
        return "double_confirm"
    if has_tool_actions or storage_target == "feishu_doc":
        return "instant_confirm"
    if target == "todo" or due_at:
        return "instant_confirm"
    if risk_level == "medium":
        return "instant_confirm"
    return "batch_confirm"


def create_agent_run(
    conn: sqlite3.Connection,
    intent: str,
    source_event_id: str,
    payload: dict,
    input_refs: list[str],
    tool_calls: list[dict],
    questions: list[str],
) -> str:
    run_id = new_id("run")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO agent_runs (
          id, intent, source_event_id, input_refs, tool_calls, candidate_output,
          questions, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate_generated', ?, ?)
        """,
        (
            run_id,
            intent,
            source_event_id,
            json.dumps(input_refs, ensure_ascii=False),
            json.dumps(tool_calls, ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False),
            json.dumps(questions, ensure_ascii=False),
            now,
            now,
        ),
    )
    audit(conn, "create_agent_run", "agent_run", run_id, {"intent": intent, "candidates": len(payload.get("candidates") or [])})
    return run_id


def create_confirmation(
    conn: sqlite3.Connection,
    risk_level: str,
    action_type: str,
    target_type: str,
    target_id: str,
    source_ref: str,
    payload: dict,
) -> str:
    confirmation_id = new_id("confirm")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO confirmations (
          id, risk_level, action_type, target_type, target_id, source_ref,
          payload, decision, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            confirmation_id,
            risk_level,
            action_type,
            target_type,
            target_id,
            source_ref,
            json.dumps(payload, ensure_ascii=False),
            now,
            now,
        ),
    )
    return confirmation_id


def resolve_pending_confirmations(conn: sqlite3.Connection, target_type: str, target_id: str, decision: str) -> None:
    now = now_iso()
    conn.execute(
        """
        UPDATE confirmations
        SET decision = ?, decided_at = ?, updated_at = ?
        WHERE target_type = ? AND target_id = ? AND decision = 'pending'
        """,
        (decision, now, now, target_type, target_id),
    )


def normalize_agent_intent(intent: str) -> str:
    value = (intent or "").strip().lower()
    aliases = {
        "task": "todo",
        "todos": "todo",
        "reminder": "todo",
        "link": "note",
        "knowledge": "note",
        "document": "note",
        "fixed": "pinned",
        "pin": "pinned",
        "sticky": "pinned",
    }
    value = aliases.get(value, value)
    return value if value in ["todo", "note", "pinned", "memo"] else "memo"


def item_type_for_target(target: str) -> str:
    if target == "todo":
        return "task_candidate"
    if target == "note":
        return "note_candidate"
    if target == "pinned":
        return "pinned_candidate"
    return "memo"


def agent_context_payload(conn: sqlite3.Connection) -> dict:
    notes = [row_to_dict(row) for row in conn.execute("SELECT * FROM notes ORDER BY updated_at DESC LIMIT 120").fetchall()]
    tasks = [row_to_dict(row) for row in conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 120").fetchall()]
    pinned = get_pinned_slots(conn)
    projects = set()
    tags = set()
    for note in notes:
        for project in note.get("projects") or []:
            projects.add(project)
        for tag in note.get("tags") or []:
            tags.add(tag)
    for task in tasks:
        if task.get("project_id"):
            projects.add(task["project_id"])
    for slot in pinned:
        if slot.get("category"):
            tags.add(slot["category"])
    return {
        "workspace": "Ayla personal agent workspace",
        "today": today_key(),
        "entrypoints": ["feishu_bot", "local_web", "browser_share", "file_drop"],
        "categories": sorted(set([*PUBLIC_CATEGORY_DIRS.keys(), *LOCAL_STATE_CATEGORY_DIRS.keys()])),
        "storage_targets": ["local_state", "feishu_doc", "obsidian_public_vault"],
        "visibility": ["private", "internal", "public"],
        "public_vault_sections": sorted(set(PUBLIC_CATEGORY_DIRS.values())),
        "local_state_sections": sorted(set(LOCAL_STATE_CATEGORY_DIRS.values())),
        "projects": sorted(projects),
        "tags": sorted(tags),
        "pinned_slots": [{"title": item["title"], "category": item["category"]} for item in pinned],
        "agent_roles": AGENT_ROLES,
        "connectors": CONNECTORS,
        "permission_policies": PERMISSION_POLICIES,
        "candidate_schema": {
            "intent": "capture|task|public_knowledge|work_record|query|summary|external_action",
            "candidates": [
                {
                    "type": "todo|public_note|work_record|pinned|memo|report_material",
                    "title": "候选标题",
                    "content": "候选内容",
                    "storage_target": "local_state|feishu_doc|obsidian_public_vault",
                    "visibility": "public|private|internal",
                    "tags": ["topic/agent"],
                    "due_at": None,
                    "source_refs": ["inbox_or_external_ref"],
                    "risk_level": "low|medium|high",
                    "requires_confirmation": True,
                }
            ],
            "questions": [],
            "tool_actions": [],
        },
        "recent_tasks": [
            {
                "title": item["title"],
                "status": item["status"],
                "due_at": item.get("due_at") or "",
                "project": item.get("project_id") or "",
            }
            for item in tasks[:20]
        ],
        "recent_notes": [
            {
                "title": item["title"],
                "type": item["type"],
                "tags": item.get("tags") or [],
                "projects": item.get("projects") or [],
            }
            for item in notes[:20]
        ],
        "rules": [
            "飞书 Bot 是主入口；本地 Web 是展示、编辑和确认层。",
            "所有输入先落 SourceEvent 和 InboxItem，再生成候选结果。",
            "公开知识只在 visibility=public 且 storage_target=obsidian_public_vault 时写入 PublicKnowledgeVault。",
            "工作沉淀、会议纪要、实验状态和 TODO 默认留在 LocalWorkState 或飞书文档引用。",
            "低风险本地写入日维度批量确认；外部写操作、DDL、删除和公开发布必须即时或二次确认。",
        ],
    }


def as_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def as_dict_list(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def legacy_candidate_from_payload(payload: dict, title: str, content: str, category: str, target: str) -> dict:
    candidate_type = {
        "todo": "todo",
        "note": "public_note" if bool(payload.get("publishable", False)) else "work_record",
        "pinned": "pinned",
        "memo": "memo",
    }.get(target, "memo")
    return {
        "type": candidate_type,
        "title": title,
        "content": content,
        "storage_target": payload.get("storage_target") or ("obsidian_public_vault" if payload.get("publishable") else "local_state"),
        "visibility": payload.get("visibility") or ("public" if payload.get("publishable") else payload.get("sensitivity") or "private"),
        "tags": payload.get("tags") or [],
        "due_at": payload.get("due_at") or "",
        "project": payload.get("project") or payload.get("project_id") or "",
        "risk_level": payload.get("risk_level") or ("medium" if is_risk_like(content) else "low"),
        "requires_confirmation": bool(payload.get("needs_review", True)),
        "source_refs": payload.get("source_refs") or [],
        "priority": payload.get("priority") or "normal",
        "category": category,
    }


def agent_ingest(conn: sqlite3.Connection, payload: dict) -> dict:
    raw_input = str(payload.get("raw_input") or payload.get("raw_text") or payload.get("input") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    base_content = str(payload.get("content") or "").strip()
    if not base_content:
        base_content = summary or raw_input
    if not raw_input:
        raw_input = base_content
    title = str(payload.get("title") or "").strip() or title_from_content(summary or base_content or raw_input, "Agent 记录")
    intent = str(payload.get("intent") or payload.get("target") or "capture").strip() or "capture"
    fallback_target = normalize_agent_intent(intent)
    category = str(payload.get("category") or "").strip() or classify_text(" ".join([title, summary, base_content, raw_input]))
    source_url = str(payload.get("source_url") or payload.get("url") or "").strip()
    tool_actions = as_dict_list(payload.get("tool_actions") or payload.get("tool_calls"))
    questions = as_string_list(payload.get("questions"))
    candidates = as_dict_list(payload.get("candidates"))
    if not candidates:
        candidates = [legacy_candidate_from_payload(payload, title, base_content, category, fallback_target)]
    source_type = str(payload.get("source") or "openclaw_agent").strip() or "openclaw_agent"
    settings = get_settings(conn)
    event_id = create_source_event(
        conn,
        source_type,
        title,
        raw_input,
        author=str(payload.get("author") or workspace_account_author(settings, "openclaw")),
        source_url=source_url,
        metadata={
            "agent_payload": payload,
            "category": category,
            "reasoning_hint": payload.get("reasoning_hint") or payload.get("reason") or "",
            "questions": questions,
            "tool_actions": tool_actions,
        },
    )
    run_id = create_agent_run(
        conn,
        intent,
        event_id,
        {"summary": summary, "candidates": candidates},
        as_string_list(payload.get("input_refs")) or [event_id],
        tool_actions,
        questions,
    )
    item_results = []
    direct_results = []
    for index, candidate in enumerate(candidates):
        candidate_type = (str(candidate.get("type") or "").strip() or fallback_target).lower()
        target = target_for_candidate(candidate_type, fallback_target)
        candidate_title = str(candidate.get("title") or "").strip() or title
        candidate_content = str(candidate.get("content") or "").strip() or base_content or summary or raw_input
        if summary and summary not in candidate_content:
            candidate_content = f"摘要：{summary}\n\n{candidate_content}"
        candidate_category = str(candidate.get("category") or category).strip() or category
        visibility = normalize_visibility(candidate.get("visibility") or payload.get("visibility") or payload.get("sensitivity"))
        storage_target = normalize_storage_target(candidate.get("storage_target") or payload.get("storage_target"), visibility)
        if candidate_type == "public_note":
            visibility = "public"
            storage_target = "obsidian_public_vault"
        if candidate_type in ["work_record", "report_material"]:
            visibility = "internal" if visibility == "private" else visibility
            storage_target = "local_state" if storage_target == "obsidian_public_vault" else storage_target
        risk_level = normalize_risk_level(candidate.get("risk_level") or payload.get("risk_level") or ("medium" if is_risk_like(candidate_content) else "low"))
        due_at = str(candidate.get("due_at") or payload.get("due_at") or "").strip()
        priority = str(candidate.get("priority") or payload.get("priority") or "normal")
        project = str(candidate.get("project") or candidate.get("project_id") or payload.get("project") or payload.get("project_id") or "").strip()
        tags = coerce_tags(candidate.get("tags") or payload.get("tags"))
        confidence = float(candidate.get("confidence") or payload.get("confidence") or 0.86)
        source_refs = as_string_list(candidate.get("source_refs")) or as_string_list(payload.get("source_refs")) or [event_id]
        requires_confirmation = bool(candidate.get("requires_confirmation", True))
        policy = confirmation_policy(target, storage_target, risk_level, bool(tool_actions), due_at)
        if policy in ["instant_confirm", "double_confirm"]:
            status = "待确认"
        else:
            status = "自动分类" if requires_confirmation else "待确认"
        item_id = create_inbox_item(
            conn,
            event_id,
            item_type_for_candidate(candidate_type, target),
            candidate_title,
            candidate_content,
            candidate_category,
            confidence,
            {
                "tags": tags,
                "project": project,
                "is_task": target == "todo",
                "risk": risk_level in ["medium", "high"] or is_risk_like(candidate_content),
                "auto_classified": True,
                "auto_target": target,
                "candidate_type": candidate_type,
                "storage_target": storage_target,
                "visibility": visibility,
                "risk_level": risk_level,
                "requires_confirmation": requires_confirmation,
                "confirmation_policy": policy,
                "source_url": str(candidate.get("source_url") or source_url),
                "source_refs": source_refs,
                "agent_run_id": run_id,
                "parser_provider": "openclaw_agent",
                "parser_status": "agent",
                "review_date": today_key(),
                "review_status": "pending",
                "suggested_priority": priority,
                "suggested_due_at": due_at,
                "sensitivity": str(candidate.get("sensitivity") or payload.get("sensitivity") or visibility),
                "publishable": storage_target == "obsidian_public_vault" and visibility == "public",
                "reasoning_hint": candidate.get("reasoning_hint") or candidate.get("reason") or payload.get("reasoning_hint") or payload.get("reason") or "",
                "questions": questions,
                "tool_actions": tool_actions,
            },
            status=status,
        )
        confirmation_id = create_confirmation(
            conn,
            risk_level,
            target,
            "inbox_item",
            item_id,
            event_id,
            {
                "policy": policy,
                "candidate_index": index,
                "candidate_type": candidate_type,
                "storage_target": storage_target,
                "visibility": visibility,
                "title": candidate_title,
                "source_refs": source_refs,
            },
        )
        direct_result = {}
        if not requires_confirmation and policy == "batch_confirm" and risk_level == "low":
            if target == "todo":
                direct_result = confirm_task(
                    conn,
                    item_id,
                    {
                        "title": candidate_title,
                        "description": candidate_content,
                        "priority": priority,
                        "due_at": due_at,
                        "project_id": project,
                    },
                )
            elif target == "note":
                direct_result = confirm_note(
                    conn,
                    item_id,
                    {
                        "title": candidate_title,
                        "content": candidate_content,
                        "category": candidate_category,
                        "tags": tags,
                        "projects": [project] if project else [],
                        "storage_target": storage_target,
                        "visibility": visibility,
                    },
                )
            elif target == "pinned":
                direct_result = create_pinned_slot(conn, {"title": candidate_title, "content": candidate_content, "category": candidate_category})
                update_inbox_status(conn, item_id, "已确认")
            else:
                update_inbox_status(conn, item_id, "已归档")
                direct_result = {"archived": True}
            direct_results.append({"inbox_item_id": item_id, "result": direct_result})
        item_results.append(
            {
                "inbox_item_id": item_id,
                "confirmation_id": confirmation_id,
                "target": target,
                "candidate_type": candidate_type,
                "storage_target": storage_target,
                "visibility": visibility,
                "risk_level": risk_level,
                "confirmation_policy": policy,
            }
        )
    audit(conn, "agent_ingest", "agent_run", run_id, {"intent": intent, "candidates": len(item_results)})
    return {
        "source_event_id": event_id,
        "agent_run_id": run_id,
        "items": item_results,
        "direct_results": direct_results,
    }


def auto_review_items(conn: sqlite3.Connection) -> list[dict]:
    items = []
    rows = conn.execute(
        "SELECT * FROM inbox_items WHERE status = '自动分类' ORDER BY created_at ASC"
    ).fetchall()
    for row in rows:
        item = row_to_dict(row)
        metadata = item.get("metadata") or {}
        item["review_date"] = metadata.get("review_date") or item["created_at"][:10]
        item["auto_target"] = metadata.get("auto_target") or "memo"
        items.append(item)
    return items


def daily_review_payload(conn: sqlite3.Connection) -> dict:
    items = auto_review_items(conn)
    dates: dict[str, int] = {}
    for item in items:
        dates[item["review_date"]] = dates.get(item["review_date"], 0) + 1
    today = today_key()
    today_items = [item for item in items if item["review_date"] == today]
    return {
        "today": today,
        "pending_count": len(items),
        "today_count": len(today_items),
        "dates": [{"date": key, "count": dates[key]} for key in sorted(dates.keys(), reverse=True)],
        "items": today_items,
    }


def row_date(value: str) -> str:
    return str(value or "")[:10]


def active_task(task: dict) -> bool:
    return task.get("status") not in ["已完成", "已取消", "已归档"]


def daily_archive_payload(conn: sqlite3.Connection, date_key: str | None = None) -> dict:
    date_key = date_key or today_key()
    events = [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT * FROM source_events
            WHERE substr(collected_at, 1, 10) = ?
            ORDER BY collected_at DESC
            LIMIT 80
            """,
            (date_key,),
        ).fetchall()
    ]
    inbox = [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT * FROM inbox_items
            WHERE substr(created_at, 1, 10) = ? OR substr(updated_at, 1, 10) = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 120
            """,
            (date_key, date_key),
        ).fetchall()
    ]
    memo_events = [
        event
        for event in events
        if event.get("source_type") in ["manual_memo", "web_memo", "local_web_model_cli", "feishu_summary_mock"]
    ]
    auto_archived = [
        item
        for item in inbox
        if item.get("status") in ["已确认", "已归档", "已忽略"]
    ]
    adjustable = [
        item
        for item in inbox
        if item.get("status") in ["自动分类", "待确认", "需补充", "未处理"]
    ]
    return {
        "date": date_key,
        "events": memo_events,
        "auto_archived": auto_archived,
        "adjustable": adjustable,
        "counts": {
            "events": len(memo_events),
            "auto_archived": len(auto_archived),
            "adjustable": len(adjustable),
        },
    }


def generate_daily_report(conn: sqlite3.Connection, date_key: str | None = None) -> str:
    date_key = date_key or today_key()
    archive = daily_archive_payload(conn, date_key)
    tasks = [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT * FROM tasks
            WHERE substr(created_at, 1, 10) = ?
               OR substr(updated_at, 1, 10) = ?
               OR substr(due_at, 1, 10) = ?
            ORDER BY
              CASE status WHEN '待办' THEN 0 WHEN '进行中' THEN 1 WHEN '已完成' THEN 2 ELSE 3 END,
              updated_at DESC
            LIMIT 80
            """,
            (date_key, date_key, date_key),
        ).fetchall()
    ]
    active = [task for task in tasks if active_task(task)]
    done = [task for task in tasks if task.get("status") == "已完成"]
    pending = archive["adjustable"]
    lines = [
        f"{date_key} 每日整理日报",
        "",
        f"- 今日备忘归档：{archive['counts']['events']} 条输入，{archive['counts']['auto_archived']} 条已处理，{archive['counts']['adjustable']} 条待调整。",
        f"- 今日 TODO：{len(active)} 条未完成，{len(done)} 条已完成。",
    ]
    if active:
        lines.append("")
        lines.append("待跟进 TODO：")
        for task in active[:8]:
            due = f"（截止 {task['due_at']}）" if task.get("due_at") else ""
            project = f" [{task['project_id']}]" if task.get("project_id") else ""
            lines.append(f"- {task['title']}{project}{due}")
    if pending:
        lines.append("")
        lines.append("需要人工调整：")
        for item in pending[:8]:
            metadata = item.get("metadata") or {}
            target = metadata.get("auto_target") or item.get("item_type")
            lines.append(f"- {item['title']} -> {target}")
    if archive["events"]:
        lines.append("")
        lines.append("今日输入摘要：")
        for event in archive["events"][:6]:
            lines.append(f"- {event.get('title') or event.get('source_type')}")
    return "\n".join(lines)


def get_daily_work_log(conn: sqlite3.Connection, date_key: str | None = None) -> dict:
    date_key = date_key or today_key()
    row = conn.execute("SELECT * FROM daily_work_logs WHERE date_key = ?", (date_key,)).fetchone()
    generated = generate_daily_report(conn, date_key)
    if not row:
        return {
            "date": date_key,
            "summary": "",
            "report": generated,
            "generated_report": generated,
            "updated_at": "",
        }
    data = row_to_dict(row)
    data["generated_report"] = generated
    if not data.get("report"):
        data["report"] = generated
    return data


def update_daily_work_log(conn: sqlite3.Connection, payload: dict) -> dict:
    date_key = str(payload.get("date") or today_key()).strip()[:10] or today_key()
    summary = str(payload.get("summary") or "").strip()
    report = str(payload.get("report") or "").strip()
    now = now_iso()
    conn.execute(
        """
        INSERT INTO daily_work_logs (date_key, summary, report, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date_key) DO UPDATE SET
          summary = excluded.summary,
          report = excluded.report,
          updated_at = excluded.updated_at
        """,
        (date_key, summary, report, now, now),
    )
    audit(conn, "update_daily_work_log", "daily_work_log", date_key, {"summary_len": len(summary), "report_len": len(report)})
    return get_daily_work_log(conn, date_key)


def confirm_daily_review(conn: sqlite3.Connection, payload: dict) -> dict:
    review_date = str(payload.get("date", "")).strip() or today_key()
    requested_ids = payload.get("ids")
    requested = set(requested_ids) if isinstance(requested_ids, list) and requested_ids else None
    overrides_raw = payload.get("overrides")
    overrides = {}
    if isinstance(overrides_raw, list):
        overrides = {
            str(item.get("id")): item
            for item in overrides_raw
            if isinstance(item, dict) and item.get("id")
        }
    items = [
        item
        for item in auto_review_items(conn)
        if item["review_date"] == review_date and (requested is None or item["id"] in requested)
    ]
    result = {"date": review_date, "todo": 0, "note": 0, "pinned": 0, "archived": 0, "total": len(items)}
    for item in items:
        override = overrides.get(item["id"], {})
        if override:
            apply_inbox_override(conn, item["id"], override)
        target = override.get("target") or item.get("auto_target") or "memo"
        if target == "todo":
            confirm_task(
                conn,
                item["id"],
                {
                    "title": override.get("title"),
                    "description": override.get("content"),
                    "priority": override.get("priority"),
                    "due_at": override.get("due_at"),
                    "project_id": override.get("project_id"),
                },
            )
            result["todo"] += 1
        elif target == "note":
            confirm_note(
                conn,
                item["id"],
                {
                    "title": override.get("title"),
                    "content": override.get("content"),
                    "category": override.get("category"),
                    "tags": override.get("tags"),
                    "projects": [override.get("project_id")] if override.get("project_id") else [],
                    "storage_target": override.get("storage_target"),
                    "visibility": override.get("visibility"),
                },
            )
            result["note"] += 1
        elif target == "pinned":
            create_pinned_slot(
                conn,
                {
                    "title": override.get("title") or item["title"],
                    "content": override.get("content") or item["content"],
                    "category": override.get("category") or item.get("suggested_category") or "待整理",
                },
            )
            update_inbox_status(conn, item["id"], "已确认")
            result["pinned"] += 1
        else:
            update_inbox_status(conn, item["id"], "已归档")
            result["archived"] += 1
    audit(conn, "confirm_daily_review", "daily_review", review_date, result)
    return result


def orchestration_payload(settings: dict) -> dict:
    return {
        "architecture": [
            {"key": "entry", "title": "多入口采集层", "detail": "飞书 Bot、本地 Web、浏览器分享和文件投递只负责采集。"},
            {"key": "inbox", "title": "Inbox 收件箱", "detail": "所有原文先成为 SourceEvent 和 InboxItem，保留来源。"},
            {"key": "agent", "title": "Agent 编排层", "detail": "Orchestrator 生成结构化候选，并记录 AgentRun。"},
            {"key": "confirm", "title": "人工确认层", "detail": "Review Agent 按风险进入批量、即时或二次确认。"},
            {"key": "storage", "title": "落库与展示层", "detail": "TODO、本地工作库、公开知识 Vault、固定便笺和报告素材分流。"},
        ],
        "agents": AGENT_ROLES,
        "connectors": CONNECTORS,
        "permission_policies": PERMISSION_POLICIES,
        "storage_roots": {
            "state_root": settings.get("state_root_path"),
            "public_vault": settings.get("public_vault_path") or settings.get("vault_path"),
            "work_library": settings.get("work_library_path"),
            "runtime": str(RUNTIME_ROOT),
        },
        "mvp_focus": [
            "P0 本地闭环：记录 -> 候选 -> 人工确认 -> TODO / 本地工作库 / 公开知识 / 固定便笺。",
            "P1 飞书接入：飞书 Bot、妙记和日历优先。",
            "P2 工作数据看板：Libra、Meego、GitHub 状态只读聚合。",
            "P3 资产化复盘：周报、月报、季度总结草稿和公开知识图谱。",
        ],
    }


def state_payload(conn: sqlite3.Connection) -> dict:
    materialized_defaults = materialize_ai_summary_defaults(conn)
    inbox = [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT * FROM inbox_items ORDER BY updated_at DESC, created_at DESC LIMIT 300"
        ).fetchall()
    ]
    tasks = [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT tasks.*, source_events.source_url AS source_url, source_events.source_type AS source_type
            FROM tasks
            LEFT JOIN source_events ON source_events.id = tasks.source_event_id
            ORDER BY tasks.updated_at DESC
            LIMIT 300
            """
        ).fetchall()
    ]
    notes = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM notes ORDER BY updated_at DESC LIMIT 200").fetchall()
    ]
    events = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM source_events ORDER BY collected_at DESC LIMIT 200").fetchall()
    ]
    audit_rows = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 50").fetchall()
    ]
    agent_runs = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT 80").fetchall()
    ]
    confirmations = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM confirmations ORDER BY updated_at DESC LIMIT 120").fetchall()
    ]
    settings = get_settings(conn)
    stats = {
        "pending_inbox": sum(1 for item in inbox if item["status"] in ["待确认", "未处理", "需补充", "自动分类"]),
        "today_tasks": sum(1 for task in tasks if task["status"] not in ["已完成", "已取消", "已归档"]),
        "notes": len(notes),
        "risks": sum(1 for item in inbox if (item.get("metadata") or {}).get("risk")),
    }
    return {
        "profile": workspace_account_payload(settings),
        "settings": settings,
        "stats": stats,
        "inbox": inbox,
        "tasks": tasks,
        "notes": notes,
        "events": events,
        "pinned_slots": get_pinned_slots(conn),
        "daily_review": daily_review_payload(conn),
        "daily_archive": daily_archive_payload(conn),
        "today_work_log": get_daily_work_log(conn),
        "audit_logs": audit_rows,
        "agent_runs": agent_runs,
        "confirmations": confirmations,
        "orchestration": orchestration_payload(settings),
        "model_cli_status": model_cli_status_payload(settings),
        "ai_summary_defaults": materialized_defaults,
        "workspace": str(ROOT),
        "vault_root": str(VAULT_ROOT),
    }


class AgentHandler(BaseHTTPRequestHandler):
    server_version = "AylaAgentMVP/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def send_json(self, payload: dict | list, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, message: str, status: int = 400) -> None:
        self.send_json({"error": message}, status=status)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)

    def request_agent_token(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        return self.headers.get("X-Ayla-Agent-Token", "").strip()

    def ensure_agent_auth(self, conn: sqlite3.Connection) -> bool:
        settings = get_settings(conn)
        expected = str(settings.get("agent_api_token") or "")
        provided = self.request_agent_token()
        if expected and provided == expected:
            return True
        self.send_error_json("invalid agent token", HTTPStatus.UNAUTHORIZED)
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "time": now_iso()})
            return
        if parsed.path == "/api/agent/context":
            with db_connect() as conn:
                if not self.ensure_agent_auth(conn):
                    return
                self.send_json(agent_context_payload(conn))
            return
        if parsed.path == "/api/connectors/lark/status":
            with db_connect() as conn:
                self.send_json(lark_connector_status(conn))
            return
        if parsed.path == "/api/connectors/libra/experiments":
            self.send_json(libra_experiments_payload(parsed.query))
            return
        if parsed.path == "/api/state":
            with db_connect() as conn:
                self.send_json(state_payload(conn))
            return
        if parsed.path.startswith("/api/notes/") and parsed.path.endswith("/raw"):
            note_id = parsed.path.split("/")[-2]
            with db_connect() as conn:
                row = conn.execute("SELECT content FROM notes WHERE id = ?", (note_id,)).fetchone()
                if not row:
                    self.send_error_json("note not found", HTTPStatus.NOT_FOUND)
                    return
                data = row["content"].encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            return
        self.serve_static(parsed.path)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ["/", "/index.html"]:
            target = WEB_ROOT / "index.html"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(target.stat().st_size if target.exists() else 0))
            self.end_headers()
            return
        if parsed.path == "/api/health":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            with db_connect() as conn:
                if parsed.path == "/api/memos":
                    content = str(payload.get("content", "")).strip()
                    if not content:
                        self.send_error_json("content is required")
                        return
                    result = memo_to_inbox(conn, content, str(payload.get("partition", "")).strip())
                    conn.commit()
                    self.send_json(result, HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/import/summary":
                    content = str(payload.get("content", "")).strip()
                    if not content:
                        self.send_error_json("content is required")
                        return
                    result = import_summary(conn, str(payload.get("title", "")).strip(), content)
                    conn.commit()
                    self.send_json(result, HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/pinned-slots":
                    result = create_pinned_slot(conn, payload)
                    conn.commit()
                    self.send_json(result, HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/tasks":
                    result = create_task(conn, payload)
                    conn.commit()
                    self.send_json(result, HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/daily-review/confirm":
                    result = confirm_daily_review(conn, payload)
                    conn.commit()
                    self.send_json(result)
                    return
                if parsed.path == "/api/daily-log":
                    result = update_daily_work_log(conn, payload)
                    conn.commit()
                    self.send_json(result)
                    return
                if parsed.path == "/api/agent/ingest":
                    if not self.ensure_agent_auth(conn):
                        return
                    result = agent_ingest(conn, payload)
                    conn.commit()
                    self.send_json(result, HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/agent/token/rotate":
                    if not self.ensure_agent_auth(conn):
                        return
                    result = rotate_agent_token(conn)
                    conn.commit()
                    self.send_json(result)
                    return
                if parsed.path == "/api/connectors/lark/sync":
                    result = sync_lark_sources(conn, payload)
                    conn.commit()
                    self.send_json(result, HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/connectors/lark/bind/start":
                    result = start_lark_binding(conn, payload)
                    conn.commit()
                    self.send_json(result, HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/connectors/lark/bind/complete":
                    result = complete_lark_binding(conn, payload)
                    conn.commit()
                    self.send_json(result)
                    return
                if parsed.path == "/api/connectors/lark/bind/claim":
                    result = claim_lark_binding(conn)
                    conn.commit()
                    self.send_json(result)
                    return
                match = re.match(r"^/api/tasks/([^/]+)/complete$", parsed.path)
                if match:
                    result = complete_task(conn, unquote(match.group(1)), payload)
                    conn.commit()
                    self.send_json(result)
                    return
                match = re.match(r"^/api/inbox/([^/]+)/confirm-task$", parsed.path)
                if match:
                    result = confirm_task(conn, unquote(match.group(1)), payload)
                    conn.commit()
                    self.send_json(result, HTTPStatus.CREATED)
                    return
                match = re.match(r"^/api/inbox/([^/]+)/confirm-note$", parsed.path)
                if match:
                    result = confirm_note(conn, unquote(match.group(1)), payload)
                    conn.commit()
                    self.send_json(result, HTTPStatus.CREATED)
                    return
                match = re.match(r"^/api/inbox/([^/]+)/(ignore|need-info|archive)$", parsed.path)
                if match:
                    status_map = {
                        "ignore": "已忽略",
                        "need-info": "需补充",
                        "archive": "已归档",
                    }
                    result = update_inbox_status(conn, unquote(match.group(1)), status_map[match.group(2)])
                    conn.commit()
                    self.send_json(result)
                    return
                if parsed.path == "/api/settings":
                    result = update_settings(conn, payload)
                    conn.commit()
                    self.send_json(result)
                    return
            self.send_error_json("route not found", HTTPStatus.NOT_FOUND)
        except json.JSONDecodeError:
            self.send_error_json("invalid json")
        except ValueError as exc:
            self.send_error_json(str(exc))
        except KeyError as exc:
            self.send_error_json(str(exc), HTTPStatus.NOT_FOUND)
        except LarkCliError as exc:
            self.send_json({"error": str(exc), "detail": exc.detail}, HTTPStatus.BAD_GATEWAY)
        except Exception as exc:  # Keep local MVP debuggable.
            self.send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            with db_connect() as conn:
                match = re.match(r"^/api/tasks/([^/]+)$", parsed.path)
                if match:
                    result = update_task(conn, unquote(match.group(1)), payload)
                    conn.commit()
                    self.send_json(result)
                    return
                match = re.match(r"^/api/pinned-slots/([^/]+)$", parsed.path)
                if match:
                    result = update_pinned_slot(conn, unquote(match.group(1)), payload)
                    conn.commit()
                    self.send_json(result)
                    return
            self.send_error_json("route not found", HTTPStatus.NOT_FOUND)
        except json.JSONDecodeError:
            self.send_error_json("invalid json")
        except KeyError as exc:
            self.send_error_json(str(exc), HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            with db_connect() as conn:
                match = re.match(r"^/api/pinned-slots/([^/]+)$", parsed.path)
                if match:
                    result = delete_pinned_slot(conn, unquote(match.group(1)))
                    conn.commit()
                    self.send_json(result)
                    return
                match = re.match(r"^/api/notes/([^/]+)$", parsed.path)
                if match:
                    result = delete_note(conn, unquote(match.group(1)))
                    conn.commit()
                    self.send_json(result)
                    return
                match = re.match(r"^/api/tasks/([^/]+)$", parsed.path)
                if match:
                    result = delete_task(conn, unquote(match.group(1)))
                    conn.commit()
                    self.send_json(result)
                    return
            self.send_error_json("route not found", HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self.send_error_json(str(exc), HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_static(self, request_path: str) -> None:
        path = request_path
        if path in ["", "/"]:
            path = "/index.html"
        target = (WEB_ROOT / path.lstrip("/")).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the personal Agent MVP workspace.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()
    init_db()
    server = ThreadingHTTPServer((args.host, args.port), AgentHandler)
    print(f"Ayla personal Agent MVP running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
