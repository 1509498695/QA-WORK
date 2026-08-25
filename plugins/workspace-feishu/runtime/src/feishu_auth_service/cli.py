from __future__ import annotations

import argparse
import json
import msvcrt
import socket
import sys
import threading
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import uvicorn

from feishu_auth_service.admin import AdminOutcome, AdminSession, create_admin_app
from feishu_auth_service.app import create_app
from feishu_auth_service.binding import BindingError, LocalBindingStore
from feishu_auth_service.config import ConfigurationError, Settings
from feishu_auth_service.feishu import FeishuAppCredentialValidator
from feishu_auth_service.profiles import LocalProfileVault, ProfileError


CONFIGURE_EXIT_CODES = {
    AdminOutcome.SAVED: 0,
    AdminOutcome.DELETED: 4,
    AdminOutcome.CANCELLED: 5,
    AdminOutcome.EXPIRED: 6,
    AdminOutcome.PENDING: 7,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="feishu-auth")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight", help="Report safe configuration readiness")
    subparsers.add_parser("serve", help="Run the local OAuth development service")
    configure_parser = subparsers.add_parser(
        "configure",
        help="Open a short-lived local administrator configuration session",
    )
    configure_parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not automatically open the browser (diagnostics only)",
    )
    args = parser.parse_args(argv)

    try:
        binding_store = LocalBindingStore.default()
    except BindingError as exc:
        _print_configuration_required(str(exc))
        return 2

    if args.command == "configure":
        return _run_configure(binding_store, open_browser=not args.no_open)

    try:
        settings = Settings.from_binding(binding_store.load())
    except (BindingError, ConfigurationError) as exc:
        _print_configuration_required(str(exc))
        return 2

    if args.command == "preflight":
        status = settings.safe_status()
        status["status"] = "ready"
        status["binding_path"] = str(binding_store.path)
        try:
            status["authorized_profiles"] = len(LocalProfileVault.default().summaries())
        except ProfileError as exc:
            status["status"] = "profile_store_error"
            status["message"] = str(exc)
            print(json.dumps(status, ensure_ascii=False, sort_keys=True))
            return 2
        print(json.dumps(status, ensure_ascii=False, sort_keys=True))
        return 0

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_level="info",
        proxy_headers=False,
        server_header=False,
    )
    return 0


def _run_configure(binding_store: LocalBindingStore, *, open_browser: bool) -> int:
    lock_path = binding_store.path.parents[2] / "admin-session.lock"
    try:
        with _configuration_process_lock(lock_path):
            return _run_configure_locked(binding_store, open_browser=open_browser)
    except BindingError as exc:
        print(
            json.dumps(
                {
                    "status": "configuration_session_already_running",
                    "message": str(exc),
                    "secrets_in_output": False,
                },
                ensure_ascii=False,
            )
        )
        return 9


def _run_configure_locked(binding_store: LocalBindingStore, *, open_browser: bool) -> int:
    if _normal_service_is_running():
        print(
            json.dumps(
                {
                    "status": "normal_service_must_stop",
                    "message": "Stop the OAuth service before opening a configuration session.",
                    "secrets_in_output": False,
                },
                ensure_ascii=False,
            )
        )
        return 3

    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind(("127.0.0.1", 0))
    listen_socket.listen(128)
    port = int(listen_socket.getsockname()[1])
    origin = f"http://127.0.0.1:{port}"
    session, bootstrap_token = AdminSession.create(expected_origin=origin, ttl_seconds=600)
    server_holder: dict[str, uvicorn.Server] = {}

    def stop_server() -> None:
        server = server_holder.get("server")
        if server is not None:
            server.should_exit = True

    app = create_admin_app(
        binding_store=binding_store,
        credential_validator=FeishuAppCredentialValidator(),
        session=session,
        profile_vault=LocalProfileVault.default(),
        on_terminal=stop_server,
    )
    config = uvicorn.Config(
        app,
        access_log=False,
        log_level="warning",
        proxy_headers=False,
        server_header=False,
    )
    server = uvicorn.Server(config)
    server_holder["server"] = server
    timer = threading.Timer(600.0, stop_server)
    timer.daemon = True
    timer.start()
    bootstrap_url = f"{origin}/bootstrap/{bootstrap_token}"
    if open_browser:
        opened = webbrowser.open(bootstrap_url, new=2)
        if not opened:
            print(
                json.dumps(
                    {
                        "status": "browser_open_failed",
                        "message": "Run the configure command again with --no-open for diagnostics.",
                        "secrets_in_output": False,
                    },
                    ensure_ascii=False,
                )
            )
            listen_socket.close()
            timer.cancel()
            return 8
    else:
        print(
            json.dumps(
                {
                    "status": "configuration_session_ready",
                    "url": bootstrap_url,
                    "expires_in_seconds": 600,
                    "ephemeral_url": True,
                    "secrets_in_output": False,
                },
                ensure_ascii=False,
            )
        )
    try:
        server.run(sockets=[listen_socket])
    finally:
        timer.cancel()
        listen_socket.close()
    outcome = session.outcome
    print(
        json.dumps(
            {
                "status": f"configuration_{outcome.value}",
                "binding_configured": binding_store.exists(),
                "secrets_in_output": False,
            },
            ensure_ascii=False,
        )
    )
    return CONFIGURE_EXIT_CODES[outcome]


@contextmanager
def _configuration_process_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream: BinaryIO = path.open("a+b")
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BindingError("Another administrator configuration session is active") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        stream.close()


def _normal_service_is_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 3000), timeout=0.25):
            return True
    except OSError:
        return False


def _print_configuration_required(message: str) -> None:
    print(
        json.dumps(
            {
                "status": "configuration_required",
                "message": message,
                "secrets_in_output": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
