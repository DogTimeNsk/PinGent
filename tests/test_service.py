import asyncio
from pathlib import Path

import pytest

from pingent.errors import (
    AuthorizationError,
    CodexError,
    CodexSessionUnavailableError,
    CodexTurnInterruptedError,
)
from pingent.models import TurnResult
from pingent.service import PingentService
from pingent.storage import SessionRepository


class FakeCodexAgent:
    def __init__(self) -> None:
        self.created_threads = 0
        self.resumed_threads: list[str] = []
        self.closed = False

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True

    async def create_thread(self) -> str:
        self.created_threads += 1
        return "thread-1"

    async def resume_thread(self, thread_id: str) -> None:
        self.resumed_threads.append(thread_id)

    async def cancel_turn(self) -> None:
        return None

    async def run_turn(self, thread_id: str, prompt: str, approval_handler) -> TurnResult:
        return TurnResult(text=f"done: {prompt}", codex_turn_id="turn-1")


class UnavailableCodexAgent(FakeCodexAgent):
    async def resume_thread(self, thread_id: str) -> None:
        raise CodexSessionUnavailableError("missing rollout")


class FailingCodexAgent(FakeCodexAgent):
    async def run_turn(self, thread_id: str, prompt: str, approval_handler) -> TurnResult:
        raise CodexError("turn did not start")


class BlockingCodexAgent(FakeCodexAgent):
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__()
        self._started = started
        self._release = release

    async def run_turn(self, thread_id: str, prompt: str, approval_handler) -> TurnResult:
        self._started.set()
        await self._release.wait()
        return await super().run_turn(thread_id, prompt, approval_handler)


class CancellableCodexAgent(FakeCodexAgent):
    def __init__(self, started: asyncio.Event, cancelled: asyncio.Event) -> None:
        super().__init__()
        self._started = started
        self._cancelled = cancelled

    async def cancel_turn(self) -> None:
        self._cancelled.set()

    async def run_turn(self, thread_id: str, prompt: str, approval_handler) -> TurnResult:
        self._started.set()
        await self._cancelled.wait()
        raise CodexTurnInterruptedError("interrupted")


class DeferredCancellableCodexAgent(FakeCodexAgent):
    def __init__(self, started: asyncio.Event, interrupted: asyncio.Event) -> None:
        super().__init__()
        self._started = started
        self._interrupted = interrupted
        self.cancel_requested = asyncio.Event()

    async def cancel_turn(self) -> None:
        self.cancel_requested.set()

    async def run_turn(self, thread_id: str, prompt: str, approval_handler) -> TurnResult:
        self._started.set()
        await self._interrupted.wait()
        raise CodexTurnInterruptedError("interrupted")


class UnresponsiveCancellationAgent(DeferredCancellableCodexAgent):
    async def cancel_turn(self) -> None:
        raise CodexError("Codex did not acknowledge the interruption")


class BrokenCancellableCodexAgent(FakeCodexAgent):
    def __init__(self, started: asyncio.Event, cancelled: asyncio.Event) -> None:
        super().__init__()
        self._started = started
        self._cancelled = cancelled

    async def cancel_turn(self) -> None:
        self._cancelled.set()
        raise CodexError("Codex did not acknowledge the interruption")

    async def run_turn(self, thread_id: str, prompt: str, approval_handler) -> TurnResult:
        self._started.set()
        await self._cancelled.wait()
        raise CodexError("Codex app-server closed its output stream")


@pytest.fixture
async def repository(tmp_path: Path) -> SessionRepository:
    result = SessionRepository(tmp_path / "pingent.sqlite3")
    await result.initialize()
    return result


@pytest.mark.asyncio
async def test_task_reuses_a_local_codex_thread(
    repository: SessionRepository, tmp_path: Path
) -> None:
    agents: list[FakeCodexAgent] = []

    def create_agent(_: Path) -> FakeCodexAgent:
        agent = FakeCodexAgent()
        agents.append(agent)
        return agent

    service = PingentService(
        repository=repository,
        agent_factory=create_agent,
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )

    first = await service.run_task(42, "first task")
    second = await service.run_task(42, "second task")

    assert first.text == "done: first task"
    assert second.text == "done: second task"
    assert agents[0].created_threads == 1
    assert agents[1].resumed_threads == ["thread-1"]
    assert all(agent.closed for agent in agents)


@pytest.mark.asyncio
async def test_task_rejects_an_unconfigured_telegram_user(
    repository: SessionRepository, tmp_path: Path
) -> None:
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )

    with pytest.raises(AuthorizationError):
        await service.run_task(100, "not allowed")

    events = await repository.audit_events()
    assert [(event.event_type, event.telegram_user_id, event.metadata) for event in events] == [
        ("authorization_denied", 100, {"action": "run_task"})
    ]


@pytest.mark.asyncio
async def test_status_tracks_whether_a_local_codex_thread_exists(
    repository: SessionRepository, tmp_path: Path
) -> None:
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )

    assert (await service.status(42)).state == "not_started"
    await service.run_task(42, "create a session")
    assert (await service.status(42)).state == "active"


@pytest.mark.asyncio
async def test_selected_workspaces_keep_independent_local_sessions(
    repository: SessionRepository, tmp_path: Path
) -> None:
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path, other_workspace],
    )

    assert await service.list_workspaces(42) == (tmp_path, other_workspace)
    await service.run_task(42, "first workspace")
    assert (await service.status(42)).state == "active"

    assert await service.select_workspace(42, 2) == other_workspace
    selected = await service.status(42)
    assert selected.workspace == other_workspace
    assert selected.state == "not_started"

    await service.run_task(42, "second workspace")
    await service.select_workspace(42, 1)
    assert (await service.status(42)).state == "active"


@pytest.mark.asyncio
async def test_tasks_queue_and_run_in_order_for_one_workspace(
    repository: SessionRepository, tmp_path: Path
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    events = []
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: BlockingCodexAgent(started, release),
        allowed_user_ids={42},
        workspaces=[tmp_path],
        task_notifier=lambda event: events.append(event),
    )

    first, first_position = await service.enqueue_task(42, "first task")
    await asyncio.wait_for(started.wait(), timeout=1)
    second, second_position = await service.enqueue_task(42, "second task")
    assert first_position == 1
    assert second_position == 1

    release.set()
    for _ in range(100):
        if [event.status for event in events].count("completed") == 2:
            break
        await asyncio.sleep(0.01)

    completed = [event for event in events if event.status == "completed"]
    assert [event.task.id for event in completed] == [first.id, second.id]


@pytest.mark.asyncio
async def test_restart_marks_running_tasks_interrupted_without_restarting_them(
    repository: SessionRepository, tmp_path: Path
) -> None:
    task = await repository.enqueue_task(42, tmp_path, "running task")
    await repository.claim_next_task(42, tmp_path)
    events = []
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path],
        task_notifier=lambda event: events.append(event),
    )

    await service.recover_tasks()

    assert [(event.task.id, event.status) for event in events] == [(task.id, "interrupted")]


@pytest.mark.asyncio
async def test_restart_marks_cancelling_tasks_interrupted(
    repository: SessionRepository, tmp_path: Path
) -> None:
    task = await repository.enqueue_task(42, tmp_path, "running task")
    running_task = await repository.claim_next_task(42, tmp_path)
    assert running_task is not None
    assert await repository.request_task_cancellation(running_task)
    events = []
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path],
        task_notifier=lambda event: events.append(event),
    )

    await service.recover_tasks()

    assert [(event.task.id, event.status) for event in events] == [(task.id, "interrupted")]


@pytest.mark.asyncio
async def test_restart_resumes_queued_tasks(repository: SessionRepository, tmp_path: Path) -> None:
    task = await repository.enqueue_task(42, tmp_path, "queued task")
    events = []
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path],
        task_notifier=lambda event: events.append(event),
    )

    await service.recover_tasks()
    for _ in range(100):
        if any(event.status == "completed" for event in events):
            break
        await asyncio.sleep(0.01)

    assert [(event.task.id, event.status) for event in events] == [
        (task.id, "running"),
        (task.id, "completed"),
    ]


@pytest.mark.asyncio
async def test_cancel_requests_interrupt_for_a_running_task(
    repository: SessionRepository, tmp_path: Path
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    events = []
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: CancellableCodexAgent(started, cancelled),
        allowed_user_ids={42},
        workspaces=[tmp_path],
        task_notifier=lambda event: events.append(event),
    )

    await service.enqueue_task(42, "long task")
    await asyncio.wait_for(started.wait(), timeout=1)
    assert await service.cancel_task(42, 1) == "cancelling"

    for _ in range(100):
        if any(event.status == "cancelled" for event in events):
            break
        await asyncio.sleep(0.01)

    assert [event.status for event in events] == ["running", "cancelled"]


@pytest.mark.asyncio
async def test_cancelling_task_is_visible_until_codex_confirms_interruption(
    repository: SessionRepository, tmp_path: Path
) -> None:
    started = asyncio.Event()
    interrupted = asyncio.Event()
    agent = DeferredCancellableCodexAgent(started, interrupted)
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: agent,
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )

    task, _ = await service.enqueue_task(42, "long task")
    await asyncio.wait_for(started.wait(), timeout=1)

    assert await service.cancel_task_by_id(42, task.id) == "cancelling"
    assert agent.cancel_requested.is_set()
    assert [item.status for item in await service.pending_tasks(42)] == ["cancelling"]
    assert await service.cancel_task_by_id(42, task.id) == "cancelling"

    interrupted.set()
    for _ in range(100):
        if not await service.pending_tasks(42):
            break
        await asyncio.sleep(0.01)

    assert not await service.pending_tasks(42)
    assert [entry.status for entry in await service.task_history(42)] == ["cancelled"]


@pytest.mark.asyncio
async def test_cancellation_is_recorded_when_codex_interrupt_request_fails(
    repository: SessionRepository, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    started = asyncio.Event()
    interrupted = asyncio.Event()
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: UnresponsiveCancellationAgent(started, interrupted),
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )

    task, _ = await service.enqueue_task(42, "long task")
    await asyncio.wait_for(started.wait(), timeout=1)

    assert await service.cancel_task_by_id(42, task.id) == "cancelling"
    assert [item.status for item in await service.pending_tasks(42)] == ["cancelling"]
    assert "Could not send a cancellation request to Codex" in caplog.text

    interrupted.set()
    for _ in range(100):
        if not await service.pending_tasks(42):
            break
        await asyncio.sleep(0.01)

    assert not await service.pending_tasks(42)


@pytest.mark.asyncio
async def test_cancelled_task_stays_cancelled_if_codex_stops_after_interrupt_failure(
    repository: SessionRepository, tmp_path: Path
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    events = []
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: BrokenCancellableCodexAgent(started, cancelled),
        allowed_user_ids={42},
        workspaces=[tmp_path],
        task_notifier=lambda event: events.append(event),
    )

    task, _ = await service.enqueue_task(42, "long task")
    await asyncio.wait_for(started.wait(), timeout=1)
    assert await service.cancel_task_by_id(42, task.id) == "cancelling"

    for _ in range(100):
        if any(event.status == "cancelled" for event in events):
            break
        await asyncio.sleep(0.01)

    assert [event.status for event in events] == ["running", "cancelled"]
    assert [entry.status for entry in await service.task_history(42)] == ["cancelled"]


@pytest.mark.asyncio
async def test_task_audit_events_exclude_prompt_and_result_text(
    repository: SessionRepository, tmp_path: Path
) -> None:
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FakeCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )

    await service.enqueue_task(42, "secret prompt")
    for _ in range(100):
        if any(event.event_type == "task_finished" for event in await repository.audit_events()):
            break
        await asyncio.sleep(0.01)

    events = await repository.audit_events()
    assert [event.event_type for event in events] == ["task_enqueued", "task_finished"]
    assert "secret prompt" not in str([event.metadata for event in events])
    assert "done: secret prompt" not in str([event.metadata for event in events])


@pytest.mark.asyncio
async def test_failed_first_turn_does_not_persist_an_unresumable_thread(
    repository: SessionRepository, tmp_path: Path
) -> None:
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: FailingCodexAgent(),
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )

    with pytest.raises(CodexError, match="turn did not start"):
        await service.run_task(42, "start a task")

    session = await repository.get_or_create(42, tmp_path)
    assert session.codex_thread_id is None


@pytest.mark.asyncio
async def test_missing_codex_thread_clears_the_stale_local_link(
    repository: SessionRepository, tmp_path: Path
) -> None:
    first_agent = FakeCodexAgent()
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: first_agent,
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )
    await service.run_task(42, "create a session")

    unavailable_agent = UnavailableCodexAgent()
    service = PingentService(
        repository=repository,
        agent_factory=lambda _: unavailable_agent,
        allowed_user_ids={42},
        workspaces=[tmp_path],
    )
    with pytest.raises(CodexSessionUnavailableError):
        await service.run_task(42, "resume a missing session")

    session = await repository.get_or_create(42, tmp_path)
    assert session.codex_thread_id is None
    assert unavailable_agent.closed
