"""Command-line entry points for a local Pingent installation."""

import argparse
import asyncio
import getpass
import sys

from pingent.config import Settings
from pingent.errors import WebUserAlreadyExistsError
from pingent.main import main as run_service
from pingent.storage import SessionRepository
from pingent.web_auth import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(prog="pingent")
    subparsers = parser.add_subparsers(dest="command")
    web_user = subparsers.add_parser("web-user", help="manage local Web SPA users")
    web_user_subparsers = web_user.add_subparsers(dest="web_user_command", required=True)
    create = web_user_subparsers.add_parser("create", help="create a local Web SPA user")
    create.add_argument("username")
    create.add_argument("--password-stdin", action="store_true")
    arguments = parser.parse_args()

    if arguments.command != "web-user":
        run_service()
        return
    if arguments.web_user_command == "create":
        asyncio.run(_create_web_user(arguments.username, arguments.password_stdin))


async def _create_web_user(username: str, password_stdin: bool) -> None:
    password = (
        sys.stdin.readline().rstrip("\n") if password_stdin else getpass.getpass("Password: ")
    )
    if not password_stdin and password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    settings = Settings()
    repository = SessionRepository(settings.database_path)
    await repository.initialize()
    try:
        user = await repository.create_web_user(username, hash_password(password))
    except (ValueError, WebUserAlreadyExistsError) as error:
        raise SystemExit(str(error)) from error
    await repository.record_audit_event("web_user_created", metadata={"username": user.username})
    print(f"Created Web SPA user: {user.username}")
