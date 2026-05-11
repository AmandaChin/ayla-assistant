#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
AGENT_PROMPT = ROOT / "orchestrator-agent.md"
EXAMPLES = ROOT / "examples"

INTENTS = {"capture", "task", "public_knowledge", "work_record", "query", "summary", "external_action"}
CANDIDATE_TYPES = {"todo", "public_note", "work_record", "report_material", "pinned", "memo"}
STORAGE_TARGETS = {"local_state", "feishu_doc", "obsidian_public_vault"}
VISIBILITY = {"private", "internal", "public"}
RISK_LEVELS = {"low", "medium", "high"}
PRIORITIES = {"low", "normal", "medium", "high", "urgent"}


class ValidationError(Exception):
    pass


def load_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def load_json(path: str) -> dict:
    text = load_text(path)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a JSON object")
    return payload


def require_string(mapping: dict, key: str, path: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{path}.{key} must be a non-empty string")
    return value


def require_list(mapping: dict, key: str, path: str) -> list:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ValidationError(f"{path}.{key} must be an array")
    return value


def validate_string_enum(value: object, allowed: set[str], path: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{path} must be a string")
    if value not in allowed:
        raise ValidationError(f"{path} must be one of {sorted(allowed)}, got {value!r}")
    return value


def validate_candidate(candidate: object, index: int) -> dict:
    if not isinstance(candidate, dict):
        raise ValidationError(f"candidates[{index}] must be an object")
    path = f"candidates[{index}]"
    candidate_type = validate_string_enum(candidate.get("type"), CANDIDATE_TYPES, f"{path}.type")
    storage_target = validate_string_enum(candidate.get("storage_target"), STORAGE_TARGETS, f"{path}.storage_target")
    visibility = validate_string_enum(candidate.get("visibility"), VISIBILITY, f"{path}.visibility")
    risk_level = validate_string_enum(candidate.get("risk_level"), RISK_LEVELS, f"{path}.risk_level")
    require_string(candidate, "title", path)
    require_string(candidate, "content", path)
    tags = require_list(candidate, "tags", path)
    source_refs = require_list(candidate, "source_refs", path)
    if not source_refs or not all(isinstance(item, str) and item.strip() for item in source_refs):
        raise ValidationError(f"{path}.source_refs must contain at least one non-empty string")
    if not all(isinstance(item, str) for item in tags):
        raise ValidationError(f"{path}.tags must contain strings")
    if not isinstance(candidate.get("requires_confirmation"), bool):
        raise ValidationError(f"{path}.requires_confirmation must be boolean")
    confidence = candidate.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise ValidationError(f"{path}.confidence must be a number from 0 to 1")
    priority = candidate.get("priority", "normal")
    if priority and priority not in PRIORITIES:
        raise ValidationError(f"{path}.priority must be one of {sorted(PRIORITIES)}")
    if candidate_type == "public_note":
        if storage_target != "obsidian_public_vault" or visibility != "public":
            raise ValidationError(f"{path} public_note must use storage_target=obsidian_public_vault and visibility=public")
    if candidate_type in {"work_record", "report_material", "todo"} and storage_target == "obsidian_public_vault":
        raise ValidationError(f"{path} internal or task candidates must not target obsidian_public_vault")
    if visibility == "public" and storage_target != "obsidian_public_vault":
        raise ValidationError(f"{path} public visibility should target obsidian_public_vault")
    return {
        "type": candidate_type,
        "storage_target": storage_target,
        "visibility": visibility,
        "risk_level": risk_level,
        "policy": infer_policy(candidate_type, storage_target, risk_level, bool(candidate.get("due_at")), False),
    }


def infer_policy(candidate_type: str, storage_target: str, risk_level: str, has_due_at: bool, has_tool_actions: bool) -> str:
    if risk_level == "high":
        return "double_confirm"
    if has_tool_actions or storage_target == "feishu_doc":
        return "instant_confirm"
    if candidate_type == "todo" or has_due_at:
        return "instant_confirm"
    if risk_level == "medium":
        return "instant_confirm"
    return "batch_confirm"


def validate_payload(payload: dict) -> list[dict]:
    require_string(payload, "raw_input", "payload")
    intent = validate_string_enum(payload.get("intent"), INTENTS, "payload.intent")
    require_string(payload, "summary", "payload")
    candidates = require_list(payload, "candidates", "payload")
    if not candidates:
        raise ValidationError("payload.candidates must not be empty")
    if not isinstance(payload.get("questions"), list):
        raise ValidationError("payload.questions must be an array")
    if not isinstance(payload.get("tool_actions"), list):
        raise ValidationError("payload.tool_actions must be an array")
    tool_actions = payload.get("tool_actions") or []
    summary = []
    for index, candidate in enumerate(candidates):
        item = validate_candidate(candidate, index)
        if tool_actions:
            item["policy"] = infer_policy(
                item["type"],
                item["storage_target"],
                item["risk_level"],
                bool(candidate.get("due_at")),
                True,
            )
        summary.append(item)
    if intent == "external_action" and not tool_actions:
        raise ValidationError("payload.intent=external_action requires non-empty tool_actions")
    return summary


def render_prompt(input_text: str, context: dict) -> str:
    prompt = AGENT_PROMPT.read_text(encoding="utf-8")
    return (
        f"{prompt}\n\n"
        "## 运行时上下文\n\n"
        "使用下面的 Ayla 工作台上下文：\n\n"
        f"```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```\n\n"
        "## 用户输入\n\n"
        f"```text\n{input_text.strip()}\n```\n\n"
        "只返回 JSON。"
    )


def http_json(method: str, url: str, token: str = "", payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["X-Ayla-Agent-Token"] = token
    req = request.Request(url, data=data, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except (error.URLError, OSError) as exc:
        body = curl_json(method, url, headers, data)
    return json.loads(body) if body else {}


def curl_json(method: str, url: str, headers: dict[str, str], data: bytes | None) -> str:
    command = ["curl", "-sS", "-X", method]
    for key, value in headers.items():
        command.extend(["-H", f"{key}: {value}"])
    if data is not None:
        command.extend(["--data-binary", "@-"])
    command.append(url)
    result = subprocess.run(
        command,
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        detail = stderr or stdout or f"curl exited with code {result.returncode}"
        raise RuntimeError(detail)
    return stdout


def is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def load_server_module():
    path = REPO_ROOT / "server.py"
    spec = importlib.util.spec_from_file_location("ayla_local_server", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load server module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def local_agent_context(token: str) -> dict:
    server = load_server_module()
    with server.db_connect() as conn:
        settings = server.get_settings(conn)
        expected = str(settings.get("agent_api_token") or "")
        if expected and token != expected:
            raise RuntimeError("invalid agent token")
        return server.agent_context_payload(conn)


def local_agent_ingest(token: str, payload: dict) -> dict:
    server = load_server_module()
    with server.db_connect() as conn:
        settings = server.get_settings(conn)
        expected = str(settings.get("agent_api_token") or "")
        if expected and token != expected:
            raise RuntimeError("invalid agent token")
        response = server.agent_ingest(conn, payload)
        conn.commit()
        return response


def cmd_validate(args: argparse.Namespace) -> int:
    payload = load_json(args.payload)
    summary = validate_payload(payload)
    print(json.dumps({"ok": True, "candidates": summary}, ensure_ascii=False, indent=2))
    return 0


def cmd_check_examples(args: argparse.Namespace) -> int:
    files = sorted(EXAMPLES.glob("*.output.json"))
    if not files:
        raise ValidationError(f"no example output files found in {EXAMPLES}")
    results = []
    for path in files:
        payload = load_json(str(path))
        summary = validate_payload(payload)
        results.append({"file": str(path.relative_to(ROOT)), "candidate_count": len(summary)})
    print(json.dumps({"ok": True, "examples": results}, ensure_ascii=False, indent=2))
    return 0


def cmd_render_prompt(args: argparse.Namespace) -> int:
    input_text = load_text(args.input)
    context = load_json(args.context)
    print(render_prompt(input_text, context))
    return 0


def cmd_fetch_context(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get("AYLA_AGENT_TOKEN", "")
    try:
        payload = http_json("GET", args.base_url.rstrip("/") + "/api/agent/context", token=token)
    except RuntimeError:
        if not is_local_base_url(args.base_url):
            raise
        payload = local_agent_context(token)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    payload = load_json(args.payload)
    summary = validate_payload(payload)
    token = args.token or os.environ.get("AYLA_AGENT_TOKEN", "")
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "validated": summary, "payload": payload}, ensure_ascii=False, indent=2))
        return 0
    if not token:
        raise ValidationError("missing token: pass --token or set AYLA_AGENT_TOKEN")
    try:
        response = http_json("POST", args.base_url.rstrip("/") + "/api/agent/ingest", token=token, payload=payload)
    except RuntimeError:
        if not is_local_base_url(args.base_url):
            raise
        response = local_agent_ingest(token, payload)
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ayla Orchestrator helper")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate an Ayla ingest payload")
    validate.add_argument("--payload", required=True, help="JSON payload path, or - for stdin")
    validate.set_defaults(func=cmd_validate)

    check = sub.add_parser("check-examples", help="validate all example output payloads")
    check.set_defaults(func=cmd_check_examples)

    render = sub.add_parser("render-prompt", help="render the Orchestrator prompt with input and context")
    render.add_argument("--input", required=True, help="input text path, or - for stdin")
    render.add_argument("--context", required=True, help="context JSON path")
    render.set_defaults(func=cmd_render_prompt)

    fetch = sub.add_parser("fetch-context", help="fetch live Ayla context")
    fetch.add_argument("--base-url", default="http://127.0.0.1:5173")
    fetch.add_argument("--token", default="")
    fetch.set_defaults(func=cmd_fetch_context)

    ingest = sub.add_parser("ingest", help="validate and submit an Ayla ingest payload")
    ingest.add_argument("--payload", required=True, help="JSON payload path, or - for stdin")
    ingest.add_argument("--base-url", default="http://127.0.0.1:5173")
    ingest.add_argument("--token", default="")
    ingest.add_argument("--dry-run", action="store_true")
    ingest.set_defaults(func=cmd_ingest)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValidationError, RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
