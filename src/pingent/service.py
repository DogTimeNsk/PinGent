"""Application service for the first Telegram-to-Codex vertical slice."""

import asyncio
import inspect
import logging
import uuid
from collections.abc import Callable
from pathlib import Path

from pingent.errors import (
    ApprovalNotFoundError,
    ApprovalRequiredError,
    AuthorizationError,
    BusySessionError,
    CodexError,
    CodexSessionUnavailableError,
    CodexTurnInterruptedError,
    TaskNotFoundError,
    WorkspaceNotFoundError,
)
from pingent.models import (
    AgentApproval,
    ApprovalDecision,
    ApprovalRequest,
    CodexAgent,
    QueuedTask,
    SessionStatus,
    TaskEvent,
    TurnResult,
)
from pingent.storage import SessionRepository

AgentFactory = Callable[[Path], CodexAgent]
ApprovalNotifier = Callable[[ApprovalRequest], object]
TaskNotifier = Callable[[TaskEvent], object]

logger = logging.getLogger(__name__)


class PingentService:
    def __init__(
        self,
        repository: SessionRepository,
        agent_factory: AgentFactory,
        allowed_user_ids: set[int],
        workspaces: list[Path],
        approval_notifier: ApprovalNotifier | None = None,
        task_notifier: TaskNotifier | None = None,
        external_actor_ids: set[int] | None = None,
    ) -> None:
        self._repository = repository
        self._agent_factory = agent_factory
        self._allowed_user_ids = allowed_user_ids
        self._web_actor_ids: set[int] = set()
        self._external_actor_ids = external_actor_ids or set()
        if not workspaces:
            raise ValueError("At least one workspace is required")
        self._workspaces = tuple(workspaces)
        self._approval_notifier = approval_notifier
        self._task_notifier = task_notifier
        self._locks: dict[tuple[int, Path], asyncio.Lock] = {}
        self._workers: dict[tuple[int, Path], asyncio.Task[None]] = {}
        self._worker_locks: dict[tuple[int, Path], asyncio.Lock] = {}
        self._active_agents: dict[tuple[int, Path], tuple[str, CodexAgent]] = {}
        self._cancel_requests: set[str] = set()
        self._pending_approvals: dict[str, tuple[int, asyncio.Future[ApprovalDecision]]] = {}

    async def run_task(
        self,
        telegram_user_id: int,
        prompt: str,
        *,
        workspace: Path | None = None,
        task_id: str | None = None,
    ) -> TurnResult:
        await self._require_authorized(telegram_user_id, "run_task")
        workspace = workspace or await self._workspace_for_user(telegram_user_id)
        lock = self._locks.setdefault((telegram_user_id, workspace), asyncio.Lock())
        if lock.locked():
            raise BusySessionError("A task is already running for this session")

        async with lock:
            session = await self._repository.get_or_create(telegram_user_id, workspace)
            agent = self._agent_factory(workspace)
            try:
                await agent.start()
                if session.codex_thread_id is None:
                    thread_id = await agent.create_thread()
                else:
                    thread_id = session.codex_thread_id
                    await agent.resume_thread(thread_id)

                if task_id is not None:
                    self._active_agents[(telegram_user_id, workspace)] = (task_id, agent)
                    if task_id in self._cancel_requests:
                        await agent.cancel_turn()
                result = await agent.run_turn(
                    thread_id, prompt, lambda approval: self._request_approval(session, approval)
                )
            except CodexSessionUnavailableError:
                await self._repository.clear_thread_id(session)
                await self._repository.record_turn(session, prompt, status="session_unavailable")
                raise
            except ApprovalRequiredError:
                await self._repository.record_turn(session, prompt, status="approval_required")
                raise
            except CodexError as error:
                if task_id is not None and task_id in self._cancel_requests:
                    await self._repository.record_turn(session, prompt, status="cancelled")
                    raise CodexTurnInterruptedError("Codex turn was interrupted") from error
                await self._repository.record_turn(session, prompt, status="failed")
                raise
            else:
                if session.codex_thread_id is None:
                    session = await self._repository.set_thread_id(session, thread_id)
                await self._repository.record_turn(
                    session,
                    prompt,
                    status="completed",
                    codex_turn_id=result.codex_turn_id,
                    result=result.text,
                )
                return result
            finally:
                if task_id is not None:
                    self._active_agents.pop((telegram_user_id, workspace), None)
                    self._cancel_requests.discard(task_id)
                await agent.close()

    async def enqueue_task(self, telegram_user_id: int, prompt: str) -> tuple[QueuedTask, int]:
        await self._require_authorized(telegram_user_id, "enqueue_task")
        workspace = await self._workspace_for_user(telegram_user_id)
        key = (telegram_user_id, workspace)
        lifecycle_lock = self._worker_locks.setdefault(key, asyncio.Lock())
        async with lifecycle_lock:
            task = await self._repository.enqueue_task(telegram_user_id, workspace, prompt)
            position = await self._repository.queued_position(task)
            await self._audit(
                "task_enqueued",
                telegram_user_id,
                task_id=task.id,
                workspace=str(workspace),
            )
            self._start_worker(key)
        return task, position

    async def recover_tasks(self) -> None:
        for task in await self._repository.interrupt_running_tasks():
            if (
                task.telegram_user_id in self._allowed_user_ids
                and task.workspace in self._workspaces
            ):
                await self._audit(
                    "task_finished", task.telegram_user_id, task_id=task.id, status="interrupted"
                )
                await self._notify_task(TaskEvent(task, "interrupted"))

        for telegram_user_id, workspace in await self._repository.queued_task_sessions():
            if not self.is_authorized(telegram_user_id) or workspace not in self._workspaces:
                continue
            key = (telegram_user_id, workspace)
            lifecycle_lock = self._worker_locks.setdefault(key, asyncio.Lock())
            async with lifecycle_lock:
                self._start_worker(key)

    async def pending_tasks(self, telegram_user_id: int) -> tuple[QueuedTask, ...]:
        await self._require_authorized(telegram_user_id, "pending_tasks")
        workspace = await self._workspace_for_user(telegram_user_id)
        return await self._repository.pending_tasks(telegram_user_id, workspace)

    async def cancel_task(self, telegram_user_id: int, number: int) -> str:
        await self._require_authorized(telegram_user_id, "cancel_task")
        tasks = await self.pending_tasks(telegram_user_id)
        if number < 1 or number > len(tasks):
            raise TaskNotFoundError("Task number is not pending")
        task = tasks[number - 1]
        if task.status == "queued":
            if await self._repository.cancel_queued_task(task):
                await self._audit("task_cancelled", telegram_user_id, task_id=task.id)
                await self._notify_task(TaskEvent(task, "cancelled"))
                return "cancelled"
        if task.status == "running":
            if not await self._repository.request_task_cancellation(task):
                raise TaskNotFoundError("Task is no longer pending")
            self._cancel_requests.add(task.id)
            await self._audit("task_cancellation_requested", telegram_user_id, task_id=task.id)
            active = self._active_agents.get((task.telegram_user_id, task.workspace))
            if active is not None and active[0] == task.id:
                try:
                    await active[1].cancel_turn()
                except CodexError:
                    logger.exception("Could not send a cancellation request to Codex")
            return "cancelling"
        if task.status == "cancelling":
            return "cancelling"
        raise TaskNotFoundError("Task is no longer pending")

    async def cancel_task_by_id(self, telegram_user_id: int, task_id: str) -> str:
        tasks = await self.pending_tasks(telegram_user_id)
        for number, task in enumerate(tasks, start=1):
            if task.id == task_id:
                return await self.cancel_task(telegram_user_id, number)
        raise TaskNotFoundError("Task is not pending")

    async def task_history(self, telegram_user_id: int):
        await self._require_authorized(telegram_user_id, "task_history")
        workspace = await self._workspace_for_user(telegram_user_id)
        return await self._repository.task_history(telegram_user_id, workspace)

    async def reset_session(self, telegram_user_id: int) -> None:
        await self._require_authorized(telegram_user_id, "reset_session")
        workspace = await self._workspace_for_user(telegram_user_id)
        await self._repository.delete(telegram_user_id, workspace)
        await self._audit("session_reset", telegram_user_id, workspace=str(workspace))

    async def status(self, telegram_user_id: int) -> SessionStatus:
        await self._require_authorized(telegram_user_id, "status")
        workspace = await self._workspace_for_user(telegram_user_id)
        session = await self._repository.get(telegram_user_id, workspace)
        return SessionStatus(
            workspace=workspace,
            state=(
                "active"
                if session is not None and session.codex_thread_id is not None
                else "not_started"
            ),
        )

    async def list_workspaces(self, telegram_user_id: int) -> tuple[Path, ...]:
        await self._require_authorized(telegram_user_id, "list_workspaces")
        return self._workspaces

    async def select_workspace(self, telegram_user_id: int, number: int) -> Path:
        await self._require_authorized(telegram_user_id, "select_workspace")
        if number < 1 or number > len(self._workspaces):
            raise WorkspaceNotFoundError("Workspace number is not configured")
        workspace = self._workspaces[number - 1]
        await self._repository.set_active_workspace(telegram_user_id, workspace)
        await self._audit("workspace_selected", telegram_user_id, workspace=str(workspace))
        return workspace

    def is_authorized(self, telegram_user_id: int) -> bool:
        return (
            telegram_user_id in self._allowed_user_ids
            or telegram_user_id in self._web_actor_ids
            or telegram_user_id in self._external_actor_ids
        )

    def register_web_actor(self, actor_id: int) -> None:
        self._web_actor_ids.add(actor_id)

    async def authorize_telegram_user(self, telegram_user_id: int, action: str) -> bool:
        if self.is_authorized(telegram_user_id):
            return True
        await self._audit("authorization_denied", telegram_user_id, action=action)
        return False

    async def _workspace_for_user(self, telegram_user_id: int) -> Path:
        selected = await self._repository.get_active_workspace(telegram_user_id)
        return selected if selected in self._workspaces else self._workspaces[0]

    def _start_worker(self, key: tuple[int, Path]) -> None:
        worker = self._workers.get(key)
        if worker is None or worker.done():
            self._workers[key] = asyncio.create_task(self._run_worker(*key))

    async def _run_worker(self, telegram_user_id: int, workspace: Path) -> None:
        key = (telegram_user_id, workspace)
        try:
            while task := await self._repository.claim_next_task(telegram_user_id, workspace):
                await self._notify_task(TaskEvent(task, "running"))
                await self._execute_queued_task(task)
        finally:
            lifecycle_lock = self._worker_locks.setdefault(key, asyncio.Lock())
            async with lifecycle_lock:
                self._workers.pop(key, None)
                if await self._repository.has_queued_tasks(telegram_user_id, workspace):
                    self._start_worker(key)

    async def _execute_queued_task(self, task: QueuedTask) -> None:
        try:
            result = await self.run_task(
                task.telegram_user_id, task.prompt, workspace=task.workspace, task_id=task.id
            )
        except CodexTurnInterruptedError:
            status = "cancelled"
            result_text = None
        except CodexSessionUnavailableError:
            status = "session_unavailable"
            result_text = None
        except ApprovalRequiredError:
            status = "approval_required"
            result_text = None
        except CodexError:
            logger.exception("Queued Codex task failed")
            status = "failed"
            result_text = None
        else:
            status = "completed"
            result_text = result.text
        await self._repository.finish_task(task, status, result_text)
        await self._audit("task_finished", task.telegram_user_id, task_id=task.id, status=status)
        await self._notify_task(TaskEvent(task, status, result_text))

    async def _notify_task(self, event: TaskEvent) -> None:
        if self._task_notifier is None:
            return
        notification = self._task_notifier(event)
        if inspect.isawaitable(notification):
            await notification

    async def resolve_approval(
        self, telegram_user_id: int, approval_id: str, decision: ApprovalDecision
    ) -> None:
        await self._require_authorized(telegram_user_id, "resolve_approval")
        pending = self._pending_approvals.get(approval_id)
        if pending is None or pending[0] != telegram_user_id:
            raise ApprovalNotFoundError("Approval is unavailable")
        if not await self._repository.resolve_approval(approval_id, telegram_user_id, decision):
            raise ApprovalNotFoundError("Approval is no longer pending")
        await self._audit(
            "approval_resolved", telegram_user_id, approval_id=approval_id, decision=decision
        )
        if not pending[1].done():
            pending[1].set_result(decision)

    async def _request_approval(self, session, approval: AgentApproval) -> ApprovalDecision:
        approval_id = uuid.uuid4().hex
        if session.telegram_user_id in self._web_actor_ids | self._external_actor_ids:
            await self._repository.create_approval(approval_id, session, approval.summary)
            await self._repository.resolve_approval(
                approval_id, session.telegram_user_id, "decline"
            )
            await self._audit(
                "approval_resolved",
                session.telegram_user_id,
                approval_id=approval_id,
                decision="decline",
                reason="web_approval_not_supported",
            )
            return "decline"
        future: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
        self._pending_approvals[approval_id] = (session.telegram_user_id, future)
        await self._repository.create_approval(approval_id, session, approval.summary)
        await self._audit("approval_requested", session.telegram_user_id, approval_id=approval_id)
        try:
            if self._approval_notifier is None:
                return "decline"
            notification = self._approval_notifier(
                ApprovalRequest(approval_id, session.telegram_user_id, approval.summary)
            )
            if inspect.isawaitable(notification):
                await notification
            try:
                return await asyncio.wait_for(future, timeout=300)
            except TimeoutError:
                await self._repository.resolve_approval(
                    approval_id, session.telegram_user_id, "decline"
                )
                await self._audit(
                    "approval_resolved",
                    session.telegram_user_id,
                    approval_id=approval_id,
                    decision="decline",
                    reason="timeout",
                )
                return "decline"
        finally:
            self._pending_approvals.pop(approval_id, None)

    async def _require_authorized(self, telegram_user_id: int, action: str) -> None:
        if not await self.authorize_telegram_user(telegram_user_id, action):
            raise AuthorizationError("Telegram user is not allowed")

    async def _audit(self, event_type: str, telegram_user_id: int, **metadata: object) -> None:
        await self._repository.record_audit_event(
            event_type,
            telegram_user_id=telegram_user_id,
            metadata=metadata,
        )
