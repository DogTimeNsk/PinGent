from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.methods.base import TelegramMethod
from aiogram.types import Update

from pingent.locales import Translator
from pingent.models import QueuedTask, TaskEvent, TurnResult
from pingent.service import PingentService
from pingent.storage import SessionRepository
from pingent.telegram import build_router, notify_task


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.methods: list[TelegramMethod[Any]] = []

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.methods.append(method)
        return True

    async def close(self) -> None:
        return None

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ):
        if False:
            yield b""


class FakeCodexAgent:
    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_thread(self) -> str:
        return "thread-1"

    async def resume_thread(self, thread_id: str) -> None:
        return None

    async def cancel_turn(self) -> None:
        return None

    async def run_turn(self, thread_id: str, prompt: str, approval_handler) -> TurnResult:
        return TurnResult(text="done", codex_turn_id="turn-1")


def _message_update(bot: Bot, *, user_id: int, text: str) -> Update:
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 0,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                "text": text,
            },
        },
        context={"bot": bot},
    )


@pytest.mark.asyncio
async def test_help_command_replies_through_the_aiogram_router(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "pingent.sqlite3")
    await repository.initialize()
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )
    session = RecordingSession()
    bot = Bot(token="123456:token", session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service, Translator("en"), None))

    try:
        await dispatcher.feed_update(bot, _message_update(bot, user_id=42, text="/help"))
    finally:
        await bot.session.close()

    assert len(session.methods) == 1
    method = session.methods[0]
    assert isinstance(method, SendMessage)
    assert method.chat_id == 42
    assert method.text is not None
    assert "/workspace <number>" in method.text
    assert "/cancel <number>" in method.text


@pytest.mark.asyncio
async def test_unauthorized_message_is_not_sent_to_telegram(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "pingent.sqlite3")
    await repository.initialize()
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )
    session = RecordingSession()
    bot = Bot(token="123456:token", session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service, Translator("en"), None))

    try:
        await dispatcher.feed_update(bot, _message_update(bot, user_id=99, text="/help"))
    finally:
        await bot.session.close()

    assert session.methods == []
    events = await repository.audit_events()
    assert [(event.event_type, event.telegram_user_id) for event in events] == [
        ("authorization_denied", 99)
    ]


@pytest.mark.asyncio
async def test_completed_task_is_sent_to_telegram_as_safe_html(tmp_path: Path) -> None:
    session = RecordingSession()
    bot = Bot(token="123456:token", session=session)
    task = QueuedTask("task-1", 42, tmp_path, "task", "running")

    try:
        await notify_task(
            bot,
            TaskEvent(task, "completed", "```text\nhello <world>\n```"),
            Translator("en"),
        )
    finally:
        await bot.session.close()

    assert len(session.methods) == 1
    method = session.methods[0]
    assert isinstance(method, SendMessage)
    assert method.parse_mode == "HTML"
    assert method.text == '<pre><code class="language-text">hello &lt;world&gt;\n</code></pre>'
