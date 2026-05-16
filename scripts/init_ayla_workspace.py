#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ayla_cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize Ayla local data for first-run setup.")
    parser.add_argument("--source-root", default=str(REPO_ROOT), help="checkout or installed runtime containing server.py")
    parser.add_argument("--data-root", default=str(REPO_ROOT / "agent-vault"), help="local data root to initialize")
    parser.add_argument("--mode", default="development", help="metadata label written to system/init.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = ayla_cli.initialize_workspace(
        source_root=Path(args.source_root),
        data_root=Path(args.data_root),
        mode=args.mode,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
