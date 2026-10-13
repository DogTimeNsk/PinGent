import asyncio
import sys
from pathlib import Path

import pytest

from pingent.config import ExternalAdapterSettings
from pingent.external import ExternalAdapterHost
from pingent.models import TurnResult
from pingent.service import PingentService
from pingent.storage import SessionRepository


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
        return TurnResult(text=prompt, codex_turn_id="turn-1")


@pytest.mark.asyncio
async def test_external_adapter_submits_a_task_with_its_configured_actor(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "pingent.sqlite3")
    await repository.initialize()
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        external_actor_ids={9001},
        workspaces=[tmp_path],
    )
    adapter = ExternalAdapterSettings(
        command=[sys.executable, str(Path(__file__).parent / "fixtures/external_adapter.py")],
        actor_id=9001,
    )
    host = ExternalAdapterHost([adapter], service)
    await host.start()
    try:
        for _ in range(100):
            history = await service.task_history(9001)
            if history:
                break
            await asyncio.sleep(0.01)
        assert [(item.prompt, item.result) for item in history] == [
            ("task from external adapter", "task from external adapter")
        ]
    finally:
        await host.close()
