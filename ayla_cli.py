#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


APP_NAME = "Ayla"
VERSION = "0.1.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def default_install_root() -> Path:
    configured = os.environ.get("AYLA_INSTALL_ROOT")
    if configured and configured.strip():
        return Path(configured).expanduser()
    return Path.home() / "Library" / "Application Support" / APP_NAME


def default_app_dir() -> Path:
    return Path.home() / "Applications"


def default_bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def expand_path(value: str | Path | None, fallback: Path) -> Path:
    if value is None or str(value).strip() == "":
        return fallback.expanduser()
    return Path(value).expanduser()


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def shell_quote(path: Path | str) -> str:
    return "'" + str(path).replace("'", "'\"'\"'") + "'"


def should_skip_runtime_path(path: Path) -> bool:
    parts = set(path.parts)
    if {".git", "agent-vault", "__pycache__", ".pytest_cache", "dist"} & parts:
        return True
    if "tests" in parts:
        return True
    if path.name in {".DS_Store"}:
        return True
    if path.suffix in {".pyc", ".pyo"}:
        return True
    return False


def copy_runtime(source_root: Path, runtime_root: Path, force: bool) -> None:
    source_root = source_root.resolve()
    runtime_root = runtime_root.expanduser()
    if runtime_root.exists():
        if not force:
            raise RuntimeError(f"runtime already exists: {runtime_root}; pass --force to replace it")
        shutil.rmtree(runtime_root)
    runtime_root.mkdir(parents=True, exist_ok=True)

    for source in source_root.iterdir():
        if should_skip_runtime_path(source.relative_to(source_root)):
            continue
        target = runtime_root / source.name
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=lambda directory, names: [
                    name
                    for name in names
                    if should_skip_runtime_path((Path(directory) / name).relative_to(source_root))
                ],
            )
        elif source.is_file():
            shutil.copy2(source, target)


def write_text(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        make_executable(path)


def build_mac_app_bundle(source_root: Path, install_root: Path, app_dir: Path) -> Path:
    app_path = app_dir / f"{APP_NAME}.app"
    builder = source_root / "scripts" / "build_macos_client.sh"
    if not builder.is_file():
        raise RuntimeError(f"missing macOS app builder: {builder}")
    result = subprocess.run(
        [
            "/bin/zsh",
            str(builder),
            "--output",
            str(app_path),
            "--install-root",
            str(install_root),
        ],
        cwd=str(source_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"builder exited with {result.returncode}").strip()
        raise RuntimeError(detail)
    return app_path


def write_cli_wrapper(install_root: Path, bin_dir: Path) -> Path:
    cli_path = bin_dir / "ayla"
    target = install_root / "app" / "ayla_cli.py"
    wrapper = f"""#!/bin/sh
export AYLA_INSTALL_ROOT="${{AYLA_INSTALL_ROOT:-{install_root}}}"
exec /usr/bin/env python3 {shell_quote(target)} "$@"
"""
    write_text(cli_path, wrapper, executable=True)
    return cli_path


def metadata_path(install_root: Path) -> Path:
    return install_root / "install.json"


def state_path(install_root: Path) -> Path:
    return install_root / "runtime" / "core-state.json"


def data_root(install_root: Path) -> Path:
    return install_root / "data"


def init_metadata_path(root: Path) -> Path:
    return root / "system" / "init.json"


def load_metadata(install_root: Path) -> dict:
    path = metadata_path(install_root)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_metadata(install_root: Path, metadata: dict) -> None:
    path = metadata_path(install_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def initialize_workspace(*, source_root: Path, data_root: Path, mode: str = "install") -> dict:
    source_root = source_root.expanduser().resolve()
    data_root = data_root.expanduser()
    if not (source_root / "server.py").is_file():
        raise RuntimeError(f"source root does not contain server.py: {source_root}")

    data_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["AYLA_HOME"] = str(data_root)
    env["AYLA_PROJECT_ROOT"] = str(source_root)
    result = subprocess.run(
        [sys.executable, "-c", "import server; server.init_db()"],
        cwd=str(source_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"init_db exited with {result.returncode}").strip()
        raise RuntimeError(detail)

    db_path = data_root / "system" / "database.sqlite"
    spaces: list[str] = []
    memory_count = 0
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            spaces = [
                row[0]
                for row in conn.execute("SELECT slug FROM knowledge_spaces ORDER BY sort_order ASC").fetchall()
            ]
            memory_count = int(conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0])
    payload = {
        "ok": True,
        "version": VERSION,
        "mode": mode,
        "initialized_at": now_iso(),
        "source_root": str(source_root),
        "data_root": str(data_root),
        "database_path": str(db_path),
        "agent_memory_root": str(data_root / "AgentMemory"),
        "local_state_root": str(data_root / "LocalWorkState"),
        "public_vault_root": str(data_root / "PublicKnowledgeVault"),
        "knowledge_spaces": spaces,
        "agent_memories": memory_count,
    }
    marker = init_metadata_path(data_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def install(
    *,
    source_root: Path,
    install_root: Path,
    app_dir: Path,
    bin_dir: Path,
    force: bool = False,
) -> dict:
    source_root = source_root.expanduser().resolve()
    install_root = install_root.expanduser()
    app_dir = app_dir.expanduser()
    bin_dir = bin_dir.expanduser()
    runtime_root = install_root / "app"

    if not (source_root / "server.py").is_file():
        raise RuntimeError(f"source root does not contain server.py: {source_root}")
    if not (source_root / "packaging" / "macos" / "app_launcher.py").is_file():
        raise RuntimeError("missing packaging/macos/app_launcher.py")
    if not (source_root / "macos" / "AylaClient" / "main.swift").is_file():
        raise RuntimeError("missing macos/AylaClient/main.swift")
    if not (source_root / "scripts" / "build_macos_client.sh").is_file():
        raise RuntimeError("missing scripts/build_macos_client.sh")

    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / "runtime").mkdir(parents=True, exist_ok=True)
    (install_root / "logs").mkdir(parents=True, exist_ok=True)
    app_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    copy_runtime(source_root, runtime_root, force=force)
    init_payload = initialize_workspace(source_root=runtime_root, data_root=data_root(install_root), mode="install")
    app_path = build_mac_app_bundle(source_root, install_root, app_dir)
    cli_path = write_cli_wrapper(install_root, bin_dir)

    payload = {
        "ok": True,
        "version": VERSION,
        "app_runtime": "swift-wkwebview",
        "installed_at": now_iso(),
        "source_root": str(source_root),
        "install_root": str(install_root),
        "runtime_root": str(runtime_root),
        "data_root": str(data_root(install_root)),
        "init": init_payload,
        "app_path": str(app_path),
        "cli_path": str(cli_path),
        "state_path": str(state_path(install_root)),
        "path_hint": f"Add {bin_dir} to PATH if ayla is not found globally.",
    }
    write_metadata(install_root, payload)
    return payload


def read_state(install_root: Path) -> dict:
    path = state_path(install_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def process_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def http_json(method: str, url: str, payload: dict | None = None, timeout: float = 2.0) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    with urlrequest.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def core_health(state: dict) -> dict:
    url = str(state.get("url") or "").rstrip("/")
    if not url:
        return {"running": False}
    try:
        payload = http_json("GET", url + "/api/health")
    except (OSError, urlerror.URLError, TimeoutError, json.JSONDecodeError):
        return {"running": False, "url": url, "pid": state.get("pid")}
    return {"running": bool(payload.get("ok")), "url": url, "pid": state.get("pid"), "health": payload}


def status_payload(install_root: Path) -> dict:
    install_root = install_root.expanduser()
    metadata = load_metadata(install_root)
    app_path = Path(metadata.get("app_path") or (default_app_dir() / f"{APP_NAME}.app"))
    cli_path = Path(metadata.get("cli_path") or (default_bin_dir() / "ayla"))
    state = read_state(install_root)
    health = core_health(state)
    pid = state.get("pid")
    return {
        "ok": True,
        "version": metadata.get("version", VERSION),
        "install_root": str(install_root),
        "installed": bool(metadata),
        "app_path": str(app_path),
        "app_exists": app_path.exists(),
        "cli_path": str(cli_path),
        "cli_exists": cli_path.exists(),
        "data_root": str(data_root(install_root)),
        "state_path": str(state_path(install_root)),
        "core": {
            "running": bool(health.get("running")) and process_running(int(pid or 0)),
            "url": health.get("url") or state.get("url"),
            "pid": pid,
        },
    }


def open_app(install_root: Path) -> dict:
    status = status_payload(install_root)
    app_path = Path(status["app_path"])
    if not app_path.exists():
        raise RuntimeError(f"Ayla.app is not installed at {app_path}; run ayla install")
    subprocess.Popen(["open", str(app_path)])
    return {"ok": True, "app_path": str(app_path)}


def wait_for_core(install_root: Path, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    last = status_payload(install_root)
    while time.time() < deadline:
        last = status_payload(install_root)
        if last["core"]["running"]:
            return last
        time.sleep(0.25)
    return last


def stop_core(install_root: Path) -> dict:
    state = read_state(install_root)
    pid = int(state.get("pid") or 0)
    if not process_running(pid):
        return {"ok": True, "stopped": False, "reason": "core is not running"}
    os.kill(pid, signal.SIGTERM)
    return {"ok": True, "stopped": True, "pid": pid}


def require_core_url(install_root: Path) -> str:
    status = status_payload(install_root)
    url = status["core"].get("url")
    if not status["core"].get("running") or not url:
        raise RuntimeError("Ayla Core is not running; run ayla open first")
    return str(url).rstrip("/")


def print_json(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    payload = install(
        source_root=expand_path(args.source_root, repo_root()),
        install_root=expand_path(args.install_root, default_install_root()),
        app_dir=expand_path(args.app_dir, default_app_dir()),
        bin_dir=expand_path(args.bin_dir, default_bin_dir()),
        force=args.force,
    )
    return print_json(payload)


def cmd_update(args: argparse.Namespace) -> int:
    install_root = expand_path(args.install_root, default_install_root())
    metadata = load_metadata(install_root)
    source_root = expand_path(args.source_root or metadata.get("source_root"), repo_root())
    app_dir = expand_path(args.app_dir or Path(metadata.get("app_path", default_app_dir() / f"{APP_NAME}.app")).parent, default_app_dir())
    bin_dir = expand_path(args.bin_dir or Path(metadata.get("cli_path", default_bin_dir() / "ayla")).parent, default_bin_dir())
    payload = install(source_root=source_root, install_root=install_root, app_dir=app_dir, bin_dir=bin_dir, force=True)
    payload["updated"] = True
    return print_json(payload)


def cmd_init(args: argparse.Namespace) -> int:
    install_root = expand_path(args.install_root, default_install_root())
    source_root = expand_path(args.source_root, repo_root())
    default_data_root = data_root(install_root) if not args.development else source_root / "agent-vault"
    payload = initialize_workspace(
        source_root=source_root,
        data_root=expand_path(args.data_root, default_data_root),
        mode=args.mode or ("development" if args.development else "manual"),
    )
    return print_json(payload)


def cmd_status(args: argparse.Namespace) -> int:
    return print_json(status_payload(expand_path(args.install_root, default_install_root())))


def cmd_doctor(args: argparse.Namespace) -> int:
    install_root = expand_path(args.install_root, default_install_root())
    status = status_payload(install_root)
    lark_cli = shutil.which("lark-cli")
    payload = {
        **status,
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "lark_cli": lark_cli or "",
        "checks": {
            "installed": status["installed"],
            "app_exists": status["app_exists"],
            "cli_exists": status["cli_exists"],
            "data_root_exists": Path(status["data_root"]).exists(),
        },
    }
    return print_json(payload)


def cmd_open(args: argparse.Namespace) -> int:
    install_root = expand_path(args.install_root, default_install_root())
    payload = open_app(install_root)
    if args.wait:
        payload["status"] = wait_for_core(install_root, timeout=args.timeout)
    return print_json(payload)


def cmd_start(args: argparse.Namespace) -> int:
    return cmd_open(args)


def cmd_stop(args: argparse.Namespace) -> int:
    return print_json(stop_core(expand_path(args.install_root, default_install_root())))


def cmd_capture(args: argparse.Namespace) -> int:
    install_root = expand_path(args.install_root, default_install_root())
    url = require_core_url(install_root)
    payload = {"content": args.text, "partition": args.partition}
    return print_json(http_json("POST", url + "/api/memos", payload=payload, timeout=20))


def cmd_sync_lark(args: argparse.Namespace) -> int:
    install_root = expand_path(args.install_root, default_install_root())
    url = require_core_url(install_root)
    payload = {"days": args.days, "calendar": not args.no_calendar, "minutes": not args.no_minutes}
    return print_json(http_json("POST", url + "/api/connectors/lark/sync", payload=payload, timeout=60))


def add_install_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--install-root", default="", help="Ayla install root; defaults to ~/Library/Application Support/Ayla")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ayla local workspace CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    install_parser = sub.add_parser("install", help="install Ayla.app and the ayla CLI")
    install_parser.add_argument("--source-root", default="", help="source checkout to install from")
    add_install_root(install_parser)
    install_parser.add_argument("--app-dir", default="", help="directory where Ayla.app is written")
    install_parser.add_argument("--bin-dir", default="", help="directory where the ayla CLI wrapper is written")
    install_parser.add_argument("--force", action="store_true", help="replace an existing installed runtime")
    install_parser.set_defaults(func=cmd_install)

    update_parser = sub.add_parser("update", help="update the installed app from the recorded source checkout")
    update_parser.add_argument("--source-root", default="", help="source checkout to update from")
    add_install_root(update_parser)
    update_parser.add_argument("--app-dir", default="")
    update_parser.add_argument("--bin-dir", default="")
    update_parser.set_defaults(func=cmd_update)

    init_parser = sub.add_parser("init", help="initialize Ayla local data directories and SQLite schema")
    init_parser.add_argument("--source-root", default="", help="source checkout or installed runtime containing server.py")
    add_install_root(init_parser)
    init_parser.add_argument("--data-root", default="", help="data root to initialize; defaults to install data root")
    init_parser.add_argument("--development", action="store_true", help="initialize ./agent-vault under the source checkout")
    init_parser.add_argument("--mode", default="", help="metadata label written to system/init.json")
    init_parser.set_defaults(func=cmd_init)

    status_parser = sub.add_parser("status", help="show install and runtime status")
    add_install_root(status_parser)
    status_parser.set_defaults(func=cmd_status)

    doctor_parser = sub.add_parser("doctor", help="run local diagnostics")
    add_install_root(doctor_parser)
    doctor_parser.set_defaults(func=cmd_doctor)

    open_parser = sub.add_parser("open", help="open Ayla.app")
    add_install_root(open_parser)
    open_parser.add_argument("--wait", action="store_true", help="wait briefly for Core to become healthy")
    open_parser.add_argument("--timeout", type=float, default=8.0)
    open_parser.set_defaults(func=cmd_open)

    start_parser = sub.add_parser("start", help="start Ayla.app and Core")
    add_install_root(start_parser)
    start_parser.add_argument("--wait", action="store_true")
    start_parser.add_argument("--timeout", type=float, default=8.0)
    start_parser.set_defaults(func=cmd_start)

    stop_parser = sub.add_parser("stop", help="stop the background Ayla Core process")
    add_install_root(stop_parser)
    stop_parser.set_defaults(func=cmd_stop)

    capture_parser = sub.add_parser("capture", help="capture a quick memo into the running workbench")
    add_install_root(capture_parser)
    capture_parser.add_argument("text")
    capture_parser.add_argument("--partition", default="")
    capture_parser.set_defaults(func=cmd_capture)

    sync_parser = sub.add_parser("sync", help="run connector sync jobs")
    sync_sub = sync_parser.add_subparsers(dest="sync_command", required=True)
    lark_parser = sync_sub.add_parser("lark", help="sync read-only Lark calendar and minutes")
    add_install_root(lark_parser)
    lark_parser.add_argument("--days", type=int, default=7)
    lark_parser.add_argument("--no-calendar", action="store_true")
    lark_parser.add_argument("--no-minutes", action="store_true")
    lark_parser.set_defaults(func=cmd_sync_lark)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
