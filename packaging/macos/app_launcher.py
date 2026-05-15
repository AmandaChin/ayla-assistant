#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


HOST = "127.0.0.1"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def install_root() -> Path:
    configured = os.environ.get("AYLA_INSTALL_ROOT")
    if configured:
        return Path(configured).expanduser()
    return app_root().parent


def runtime_root() -> Path:
    return install_root() / "runtime"


def data_root() -> Path:
    return install_root() / "data"


def logs_root() -> Path:
    return install_root() / "logs"


def state_path() -> Path:
    return runtime_root() / "core-state.json"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def read_state() -> dict:
    path = state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_state(payload: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def health_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        with urlrequest.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urlerror.URLError, TimeoutError, json.JSONDecodeError):
        return False
    return bool(payload.get("ok"))


def open_workspace(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", url])
        return
    webbrowser.open(url)


def wait_for_health(url: str, seconds: float = 8.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if health_ok(url):
            return True
        time.sleep(0.2)
    return False


def start_core() -> dict:
    existing = read_state()
    existing_url = str(existing.get("url") or "")
    existing_pid = int(existing.get("pid") or 0)
    if existing_url and process_running(existing_pid) and health_ok(existing_url):
        return existing

    runtime_root().mkdir(parents=True, exist_ok=True)
    data_root().mkdir(parents=True, exist_ok=True)
    logs_root().mkdir(parents=True, exist_ok=True)

    port = find_free_port()
    url = f"http://{HOST}:{port}"
    log_path = logs_root() / "core.log"
    env = os.environ.copy()
    env["AYLA_HOME"] = str(data_root())
    env["AYLA_INSTALL_ROOT"] = str(install_root())
    command = [sys.executable, str(app_root() / "server.py"), "--host", HOST, "--port", str(port)]
    log_file = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=str(app_root()),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    state = {
        "pid": process.pid,
        "host": HOST,
        "port": port,
        "url": url,
        "started_at": now_iso(),
        "install_root": str(install_root()),
        "app_root": str(app_root()),
        "data_root": str(data_root()),
        "log_path": str(log_path),
    }
    write_state(state)
    wait_for_health(url)
    return state


def main() -> int:
    state = start_core()
    if os.environ.get("AYLA_NO_OPEN", "").lower() not in {"1", "true", "yes"}:
        open_workspace(str(state["url"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
