from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import uvicorn

from qa_skill_hub_control_plane.app import create_app
from qa_skill_hub_control_plane.settings import ControlPlaneSettings
from qa_skill_hub_control_plane.store import ControlPlaneStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the QA Skill Hub control plane")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=3100, type=int)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--state-path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if args.host != "127.0.0.1":
        raise SystemExit("the development control plane only binds to 127.0.0.1")
    settings = ControlPlaneSettings.from_env(database_path=args.state_path)
    settings = replace(settings, public_origin=f"http://{args.host}:{args.port}")
    store = ControlPlaneStore(settings.database_path)
    store.initialize()
    bootstrap_token = store.issue_bootstrap(ttl_seconds=settings.bootstrap_ttl_seconds)
    bootstrap_url = (
        f"{settings.public_origin}/bootstrap/{bootstrap_token}"
        if bootstrap_token is not None
        else None
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "origin": settings.public_origin,
                "platform_bootstrapped": bootstrap_url is None,
                "bootstrap_url": bootstrap_url,
                "state_path": str(settings.database_path),
                "secrets_in_output": bootstrap_url is not None,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if args.open_browser:
        url = bootstrap_url or f"{settings.public_origin}/account"
        threading.Timer(0.8, lambda: webbrowser.open(url, new=2)).start()
    uvicorn.run(
        create_app(settings, store=store, bootstrap_token=bootstrap_token),
        host=args.host,
        port=args.port,
        access_log=False,
    )
    return 0
