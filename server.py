#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import mimetypes
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
VAULT_ROOT = ROOT / "agent-vault"
DB_PATH = VAULT_ROOT / "system" / "database.sqlite"

DEFAULT_SETTINGS = {
    "vault_path": str(VAULT_ROOT / "obsidian"),
    "summary_frequency": "manual",
    "model_provider": "manual-rules",
    "feishu_enabled": False,
    "github_repo": "",
    "agent_api_token": "",
}

CATEGORY_DIRS = {
    "工作": "work",
    "学习": "study",
    "项目": "projects",
    "方法论": "methods",
    "会议": "meetings",
    "人物": "people",
    "个人": "personal",
    "待整理": "inbox",
    "可公开": "publishable_candidates",
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
            """
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
    for key in ["metadata", "tags", "projects", "source_event_ids", "input_ids", "detail"]:
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
    metadata: dict | None = None,
) -> str:
    event_id = new_id("src")
    now = now_iso()
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
            event_id,
            source_url,
            title,
            content,
            author,
            now,
            now,
            json.dumps(metadata or {}, ensure_ascii=False),
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


def memo_to_inbox(conn: sqlite3.Connection, content: str, partition: str = "") -> dict:
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
            "source_url": source_url,
            "parser_provider": "local-web-parser",
            "parser_status": "failed" if link_enrichment and link_enrichment["fetch_error"] else "parsed" if link_enrichment else "none",
            "review_date": today_key(),
            "review_status": "pending",
            "suggested_priority": infer_priority(content),
            "suggested_due_at": infer_due(content),
        },
        status="自动分类",
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
            "risk": is_risk_like(content),
            "source_index": ["mock-summary"],
        },
    )
    task_ids = []
    for line in split_task_lines(content):
        task_ids.append(
            create_inbox_item(
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
                    "suggested_priority": infer_priority(line),
                    "suggested_due_at": infer_due(line),
                    "source_index": ["mock-summary"],
                },
            )
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
    created_at: str,
) -> str:
    links = []
    for project in projects:
        links.append(f"[[{project}]]")
    body = body.strip()
    if links:
        body = f"关联项目：{' '.join(links)}\n\n{body}"
    return (
        "---\n"
        f"title: {yaml_scalar(title)}\n"
        f"type: {yaml_scalar(note_type)}\n"
        f"tags: {yaml_list(tags)}\n"
        f"projects: {yaml_list(projects)}\n"
        f"source: {yaml_scalar(source_label)}\n"
        f"source_url: {yaml_scalar(source_url)}\n"
        f"created_at: {yaml_scalar(created_at)}\n"
        f"updated_at: {yaml_scalar(created_at)}\n"
        f"sensitivity: {yaml_scalar(sensitivity)}\n"
        f"publishable: {'true' if publishable else 'false'}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )


def write_note_file(settings: dict, category: str, title: str, markdown: str) -> Path:
    vault = Path(str(settings.get("vault_path") or DEFAULT_SETTINGS["vault_path"])).expanduser()
    if not vault.is_absolute():
        vault = ROOT / vault
    section = CATEGORY_DIRS.get(category, "inbox")
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
    task_id = new_id("task")
    now = now_iso()
    title = (payload.get("title") or item["title"]).strip()
    conn.execute(
        """
        INSERT INTO tasks (
          id, title, description, status, priority, due_at, project_id,
          assignee, source_event_id, source_title, created_at, updated_at
        )
        VALUES (?, ?, ?, '待办', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            title,
            payload.get("description") or item["content"],
            payload.get("priority") or metadata.get("suggested_priority") or "normal",
            payload.get("due_at") or metadata.get("suggested_due_at") or "",
            payload.get("project_id") or metadata.get("project") or "",
            payload.get("assignee") or "me",
            item["source_event_id"],
            source_title(conn, item["source_event_id"]),
            now,
            now,
        ),
    )
    conn.execute(
        "UPDATE inbox_items SET status = '已确认', updated_at = ? WHERE id = ?",
        (now, item_id),
    )
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
    sensitivity = payload.get("sensitivity") or "private"
    publishable = bool(payload.get("publishable", False))
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
        created_at,
    )
    path = write_note_file(settings, category, title, markdown)
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
    conn.execute(
        "UPDATE inbox_items SET status = '已确认', updated_at = ? WHERE id = ?",
        (created_at, item_id),
    )
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
    audit(conn, "update_inbox_status", "inbox_item", item_id, {"status": status})
    return {"id": item_id, "status": status}


def update_task(conn: sqlite3.Connection, task_id: str, payload: dict) -> dict:
    allowed = ["title", "description", "status", "priority", "due_at", "project_id", "assignee"]
    updates = []
    values = []
    for key in allowed:
        if key in payload:
            updates.append(f"{key} = ?")
            values.append(payload[key])
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


def delete_note(conn: sqlite3.Connection, note_id: str) -> dict:
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        raise KeyError("note not found")
    settings = get_settings(conn)
    note_path = Path(row["path"]).expanduser()
    allowed_roots = []
    for root_value in [settings.get("vault_path"), str(VAULT_ROOT / "obsidian")]:
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
        "categories": list(CATEGORY_DIRS.keys()),
        "obsidian_sections": sorted(set(CATEGORY_DIRS.values())),
        "projects": sorted(projects),
        "tags": sorted(tags),
        "pinned_slots": [{"title": item["title"], "category": item["category"]} for item in pinned],
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
            "OpenClaw Agent 输出默认进入今日增量整理，除非明确 needs_review=false。",
            "包含 did/uid/常用命令等长期稳定信息时，优先 intent=pinned。",
            "网页链接、飞书文档、文章摘要、待读资料优先 intent=note。",
            "带截止时间、提醒、需要跟进的行动项优先 intent=todo。",
            "公开发布相关内容默认 sensitivity=private 且 publishable=false。",
        ],
    }


def agent_ingest(conn: sqlite3.Connection, payload: dict) -> dict:
    raw_input = str(payload.get("raw_input") or payload.get("raw_text") or payload.get("input") or "").strip()
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not content:
        content = summary or raw_input
    if not raw_input:
        raw_input = content
    if not title:
        title = title_from_content(summary or content or raw_input, "Agent 记录")
    target = normalize_agent_intent(str(payload.get("intent") or payload.get("target") or "memo"))
    category = str(payload.get("category") or "").strip() or classify_text(" ".join([title, summary, content, raw_input]))
    tags = coerce_tags(payload.get("tags"))
    project = str(payload.get("project") or payload.get("project_id") or "").strip()
    source_url = str(payload.get("source_url") or payload.get("url") or "").strip()
    confidence = float(payload.get("confidence") or 0.86)
    needs_review = bool(payload.get("needs_review", True))
    if summary and summary not in content:
        content = f"摘要：{summary}\n\n{content}"
    source_type = str(payload.get("source") or "openclaw_agent").strip() or "openclaw_agent"
    event_id = create_source_event(
        conn,
        source_type,
        title,
        raw_input,
        author=str(payload.get("author") or "openclaw"),
        source_url=source_url,
        metadata={
            "agent_payload": payload,
            "tags": tags,
            "project": project,
            "category": category,
            "reasoning_hint": payload.get("reasoning_hint") or payload.get("reason") or "",
        },
    )
    item_id = create_inbox_item(
        conn,
        event_id,
        item_type_for_target(target),
        title,
        content,
        category,
        confidence,
        {
            "tags": tags,
            "project": project,
            "is_task": target == "todo",
            "risk": is_risk_like(content),
            "auto_classified": True,
            "auto_target": target,
            "source_url": source_url,
            "parser_provider": "openclaw_agent",
            "parser_status": "agent",
            "review_date": today_key(),
            "review_status": "pending",
            "suggested_priority": str(payload.get("priority") or "normal"),
            "suggested_due_at": str(payload.get("due_at") or ""),
            "sensitivity": str(payload.get("sensitivity") or "private"),
            "publishable": bool(payload.get("publishable", False)),
            "reasoning_hint": payload.get("reasoning_hint") or payload.get("reason") or "",
        },
        status="自动分类",
    )
    direct_result = {}
    if needs_review is False:
        if target == "todo":
            direct_result = confirm_task(
                conn,
                item_id,
                {
                    "title": title,
                    "description": content,
                    "priority": payload.get("priority"),
                    "due_at": payload.get("due_at"),
                    "project_id": project,
                },
            )
        elif target == "note":
            direct_result = confirm_note(
                conn,
                item_id,
                {
                    "title": title,
                    "content": content,
                    "category": category,
                    "tags": tags,
                    "projects": [project] if project else [],
                    "sensitivity": payload.get("sensitivity") or "private",
                    "publishable": bool(payload.get("publishable", False)),
                },
            )
        elif target == "pinned":
            direct_result = create_pinned_slot(conn, {"title": title, "content": content, "category": category})
            update_inbox_status(conn, item_id, "已确认")
        else:
            update_inbox_status(conn, item_id, "已归档")
            direct_result = {"archived": True}
    audit(conn, "agent_ingest", "inbox_item", item_id, {"target": target, "needs_review": needs_review})
    return {
        "source_event_id": event_id,
        "inbox_item_id": item_id,
        "target": target,
        "needs_review": needs_review,
        "direct_result": direct_result,
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


def state_payload(conn: sqlite3.Connection) -> dict:
    inbox = [
        row_to_dict(row)
        for row in conn.execute(
            "SELECT * FROM inbox_items ORDER BY updated_at DESC, created_at DESC LIMIT 300"
        ).fetchall()
    ]
    tasks = [
        row_to_dict(row)
        for row in conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 300").fetchall()
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
    stats = {
        "pending_inbox": sum(1 for item in inbox if item["status"] in ["待确认", "未处理", "需补充", "自动分类"]),
        "today_tasks": sum(1 for task in tasks if task["status"] not in ["已完成", "已取消", "已归档"]),
        "notes": len(notes),
        "risks": sum(1 for item in inbox if (item.get("metadata") or {}).get("risk")),
    }
    return {
        "settings": get_settings(conn),
        "stats": stats,
        "inbox": inbox,
        "tasks": tasks,
        "notes": notes,
        "events": events,
        "pinned_slots": get_pinned_slots(conn),
        "daily_review": daily_review_payload(conn),
        "audit_logs": audit_rows,
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
                if parsed.path == "/api/daily-review/confirm":
                    result = confirm_daily_review(conn, payload)
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
        except KeyError as exc:
            self.send_error_json(str(exc), HTTPStatus.NOT_FOUND)
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
