"""Small domain objects shared by the application services."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

ApprovalDecision = Literal["accept", "decline"]
TaskStatus = Literal[
    "queued",
    "running",
    "cancelling",
    "completed",
    "failed",
    "approval_required",
    "session_unavailable",
    "interrupted",
    "cancelled",
]


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    telegram_user_id: int
    workspace: Path
    codex_thread_id: str | None


@dataclass(frozen=True, slots=True)
class TurnResult:
    text: str
    codex_turn_id: str


@dataclass(frozen=True, slots=True)
class SessionStatus:
    workspace: Path
    state: Literal["active", "not_started"]


@dataclass(frozen=True, slots=True)
class QueuedTask:
    id: str
    telegram_user_id: int
    workspace: Path
    prompt: str
    status: TaskStatus


@dataclass(frozen=True, slots=True)
class TaskHistoryEntry:
    id: str
    workspace: Path
    prompt: str
    status: TaskStatus
    result: str | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class TaskEvent:
    task: QueuedTask
    status: TaskStatus
    result: str | None = None


@dataclass(frozen=True, slots=True)
class AgentApproval:
    request_id: int | str
    summary: str


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    id: str
    telegram_user_id: int
    summary: str


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: int
    occurred_at: str
    event_type: str
    telegram_user_id: int | None
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class WebUser:
    id: str
    username: str
    is_active: bool
    actor_id: int


@dataclass(frozen=True, slots=True)
class WebSession:
    token: str
    csrf_token: str
    user: WebUser


class CodexAgent(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def create_thread(self) -> str: ...

    async def resume_thread(self, thread_id: str) -> None: ...

    async def cancel_turn(self) -> None: ...

    async def run_turn(
        self, thread_id: str, prompt: str, approval_handler: "ApprovalHandler"
    ) -> TurnResult: ...


class ApprovalHandler(Protocol):
    async def __call__(self, approval: AgentApproval) -> ApprovalDecision: ...
