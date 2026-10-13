"""SQLite persistence for local Pingent sessions, tasks, and audit history."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from pingent.errors import WebUserAlreadyExistsError
from pingent.models import (
    AuditEvent,
    QueuedTask,
    Session,
    TaskHistoryEntry,
    TaskStatus,
    WebSession,
    WebUser,
)

MIGRATIONS = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        telegram_user_id INTEGER NOT NULL,
        workspace TEXT NOT NULL,
        codex_thread_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (telegram_user_id, workspace)
    );

    CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        codex_turn_id TEXT,
        prompt TEXT NOT NULL,
        result TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        telegram_user_id INTEGER NOT NULL,
        summary TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT
    );

    CREATE TABLE IF NOT EXISTS active_workspaces (
        telegram_user_id INTEGER PRIMARY KEY,
        workspace TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        telegram_user_id INTEGER NOT NULL,
        workspace TEXT NOT NULL,
        prompt TEXT NOT NULL,
        status TEXT NOT NULL,
        result TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT
    );

    CREATE INDEX IF NOT EXISTS tasks_next_queued
    ON tasks (telegram_user_id, workspace, status, created_at);
    """,
    """
    CREATE TABLE audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        occurred_at TEXT NOT NULL,
        event_type TEXT NOT NULL,
        telegram_user_id INTEGER,
        metadata_json TEXT NOT NULL
    );

    CREATE INDEX audit_events_by_user_time
    ON audit_events (telegram_user_id, occurred_at);
    """,
    """
    CREATE TABLE web_users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        password_hash TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE web_sessions (
        id TEXT PRIMARY KEY,
        web_user_id TEXT NOT NULL REFERENCES web_users(id),
        token_hash TEXT NOT NULL UNIQUE,
        csrf_token TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );

    CREATE INDEX web_sessions_by_user ON web_sessions (web_user_id, expires_at);
    """,
    """
    ALTER TABLE web_users ADD COLUMN actor_id INTEGER;
    UPDATE web_users SET actor_id = -rowid WHERE actor_id IS NULL;
    CREATE UNIQUE INDEX web_users_actor_id ON web_users (actor_id);
    """,
)


class SessionRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    async def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            await connection.commit()
            cursor = await connection.execute("SELECT version FROM schema_migrations")
            applied_versions = {row[0] for row in await cursor.fetchall()}
            for version, migration in enumerate(MIGRATIONS, start=1):
                if version in applied_versions:
                    continue
                applied_at = self._now()
                await connection.executescript(
                    f"""
                    BEGIN IMMEDIATE;
                    {migration}
                    INSERT INTO schema_migrations (version, applied_at)
                    VALUES ({version}, '{applied_at}');
                    COMMIT;
                    """
                )

    async def record_audit_event(
        self,
        event_type: str,
        *,
        telegram_user_id: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                INSERT INTO audit_events (occurred_at, event_type, telegram_user_id, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    self._now(),
                    event_type,
                    telegram_user_id,
                    json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")),
                ),
            )
            await connection.commit()

    async def audit_events(self) -> tuple[AuditEvent, ...]:
        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT id, occurred_at, event_type, telegram_user_id, metadata_json
                FROM audit_events ORDER BY id
                """
            )
            return tuple(
                AuditEvent(
                    id=row["id"],
                    occurred_at=row["occurred_at"],
                    event_type=row["event_type"],
                    telegram_user_id=row["telegram_user_id"],
                    metadata=json.loads(row["metadata_json"]),
                )
                for row in await cursor.fetchall()
            )

    async def create_web_user(self, username: str, password_hash: str) -> WebUser:
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                "SELECT COALESCE(MIN(actor_id), 0) - 1 FROM web_users"
            )
            actor_id = (await cursor.fetchone())[0]
        user = WebUser(id=str(uuid.uuid4()), username=username, is_active=True, actor_id=actor_id)
        now = self._now()
        try:
            async with aiosqlite.connect(self._database_path) as connection:
                await connection.execute(
                    """
                    INSERT INTO web_users (
                        id, username, password_hash, is_active, actor_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user.id,
                        user.username,
                        password_hash,
                        user.is_active,
                        user.actor_id,
                        now,
                        now,
                    ),
                )
                await connection.commit()
        except aiosqlite.IntegrityError as error:
            raise WebUserAlreadyExistsError(f"Web user already exists: {username}") from error
        return user

    async def active_web_actor_ids(self) -> set[int]:
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                "SELECT actor_id FROM web_users WHERE is_active = 1 AND actor_id IS NOT NULL"
            )
            return {row[0] for row in await cursor.fetchall()}

    async def authenticate_web_user(self, username: str, password: str) -> WebUser | None:
        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT id, username, password_hash, is_active, actor_id
                FROM web_users WHERE username = ?
                """,
                (username,),
            )
            row = await cursor.fetchone()
        if row is None or not row["is_active"]:
            return None
        from pingent.web_auth import verify_password

        if not verify_password(row["password_hash"], password):
            return None
        return WebUser(
            id=row["id"], username=row["username"], is_active=True, actor_id=row["actor_id"]
        )

    async def create_web_session(self, user: WebUser) -> WebSession:
        from pingent.web_auth import hash_secret, new_secret

        token, csrf_token = new_secret(), new_secret()
        now = datetime.now(UTC)
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                INSERT INTO web_sessions (
                    id, web_user_id, token_hash, csrf_token, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    user.id,
                    hash_secret(token),
                    csrf_token,
                    now.isoformat(),
                    (now + timedelta(hours=12)).isoformat(),
                ),
            )
            await connection.commit()
        return WebSession(token=token, csrf_token=csrf_token, user=user)

    async def get_web_session(self, token: str) -> WebSession | None:
        from pingent.web_auth import hash_secret

        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT u.id, u.username, u.is_active, u.actor_id, s.csrf_token
                FROM web_sessions s JOIN web_users u ON u.id = s.web_user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                """,
                (hash_secret(token), self._now()),
            )
            row = await cursor.fetchone()
        if row is None or not row["is_active"]:
            return None
        return WebSession(
            token=token,
            csrf_token=row["csrf_token"],
            user=WebUser(
                id=row["id"], username=row["username"], is_active=True, actor_id=row["actor_id"]
            ),
        )

    async def delete_web_session(self, token: str) -> None:
        from pingent.web_auth import hash_secret

        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                "DELETE FROM web_sessions WHERE token_hash = ?", (hash_secret(token),)
            )
            await connection.commit()

    async def get_or_create(self, telegram_user_id: int, workspace: Path) -> Session:
        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            row = await self._find_session(connection, telegram_user_id, workspace)
            if row is not None:
                return self._session_from_row(row)

            session = Session(
                id=str(uuid.uuid4()),
                telegram_user_id=telegram_user_id,
                workspace=workspace,
                codex_thread_id=None,
            )
            now = self._now()
            await connection.execute(
                """
                INSERT INTO sessions (
                    id, telegram_user_id, workspace, codex_thread_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session.id, session.telegram_user_id, str(workspace), None, now, now),
            )
            await connection.commit()
            return session

    async def get(self, telegram_user_id: int, workspace: Path) -> Session | None:
        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            row = await self._find_session(connection, telegram_user_id, workspace)
            return self._session_from_row(row) if row is not None else None

    async def get_active_workspace(self, telegram_user_id: int) -> Path | None:
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                "SELECT workspace FROM active_workspaces WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
            row = await cursor.fetchone()
            return Path(row[0]) if row is not None else None

    async def set_active_workspace(self, telegram_user_id: int, workspace: Path) -> None:
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                INSERT INTO active_workspaces (telegram_user_id, workspace, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    workspace = excluded.workspace,
                    updated_at = excluded.updated_at
                """,
                (telegram_user_id, str(workspace), self._now()),
            )
            await connection.commit()

    async def enqueue_task(self, telegram_user_id: int, workspace: Path, prompt: str) -> QueuedTask:
        task = QueuedTask(
            id=str(uuid.uuid4()),
            telegram_user_id=telegram_user_id,
            workspace=workspace,
            prompt=prompt,
            status="queued",
        )
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                INSERT INTO tasks (id, telegram_user_id, workspace, prompt, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.telegram_user_id,
                    str(task.workspace),
                    task.prompt,
                    task.status,
                    self._now(),
                ),
            )
            await connection.commit()
        return task

    async def queued_position(self, task: QueuedTask) -> int:
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE telegram_user_id = ? AND workspace = ?
                    AND status = 'queued' AND (created_at, id) <= (
                        SELECT created_at, id FROM tasks WHERE id = ?
                    )
                """,
                (task.telegram_user_id, str(task.workspace), task.id),
            )
            row = await cursor.fetchone()
            return int(row[0])

    async def claim_next_task(self, telegram_user_id: int, workspace: Path) -> QueuedTask | None:
        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                """
                SELECT id, telegram_user_id, workspace, prompt, status
                FROM tasks
                WHERE telegram_user_id = ? AND workspace = ? AND status = 'queued'
                ORDER BY created_at, id
                LIMIT 1
                """,
                (telegram_user_id, str(workspace)),
            )
            row = await cursor.fetchone()
            if row is None:
                await connection.commit()
                return None
            await connection.execute(
                "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?",
                (self._now(), row["id"]),
            )
            await connection.commit()
            return self._task_from_row(row, status="running")

    async def finish_task(
        self, task: QueuedTask, status: TaskStatus, result: str | None = None
    ) -> None:
        if status in {"queued", "running"}:
            raise ValueError("A task can only be finished with a terminal status")
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                "UPDATE tasks SET status = ?, result = ?, completed_at = ? WHERE id = ?",
                (status, result, self._now(), task.id),
            )
            await connection.commit()

    async def interrupt_running_tasks(self) -> tuple[QueuedTask, ...]:
        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT id, telegram_user_id, workspace, prompt, status
                FROM tasks WHERE status IN ('running', 'cancelling')
                """
            )
            rows = await cursor.fetchall()
            await connection.execute(
                """
                UPDATE tasks SET status = 'interrupted', completed_at = ?
                WHERE status IN ('running', 'cancelling')
                """,
                (self._now(),),
            )
            await connection.commit()
            return tuple(self._task_from_row(row, status="interrupted") for row in rows)

    async def queued_task_sessions(self) -> tuple[tuple[int, Path], ...]:
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                """
                SELECT DISTINCT telegram_user_id, workspace FROM tasks
                WHERE status = 'queued'
                """
            )
            return tuple((row[0], Path(row[1])) for row in await cursor.fetchall())

    async def has_queued_tasks(self, telegram_user_id: int, workspace: Path) -> bool:
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM tasks
                    WHERE telegram_user_id = ? AND workspace = ? AND status = 'queued'
                )
                """,
                (telegram_user_id, str(workspace)),
            )
            row = await cursor.fetchone()
            return bool(row[0])

    async def pending_tasks(self, telegram_user_id: int, workspace: Path) -> tuple[QueuedTask, ...]:
        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT id, telegram_user_id, workspace, prompt, status FROM tasks
                WHERE telegram_user_id = ? AND workspace = ?
                    AND status IN ('running', 'cancelling', 'queued')
                ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'cancelling' THEN 1 ELSE 2 END,
                    created_at, id
                """,
                (telegram_user_id, str(workspace)),
            )
            return tuple(
                self._task_from_row(row, status=row["status"]) for row in await cursor.fetchall()
            )

    async def task_history(
        self, telegram_user_id: int, workspace: Path, *, limit: int = 50
    ) -> tuple[TaskHistoryEntry, ...]:
        async with aiosqlite.connect(self._database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT id, workspace, prompt, status, result, created_at, completed_at
                FROM tasks
                WHERE telegram_user_id = ? AND workspace = ?
                    AND status NOT IN ('queued', 'running')
                ORDER BY completed_at DESC, id DESC
                LIMIT ?
                """,
                (telegram_user_id, str(workspace), limit),
            )
            return tuple(
                TaskHistoryEntry(
                    id=row["id"],
                    workspace=Path(row["workspace"]),
                    prompt=row["prompt"],
                    status=row["status"],
                    result=row["result"],
                    created_at=row["created_at"],
                    completed_at=row["completed_at"],
                )
                for row in await cursor.fetchall()
            )

    async def cancel_queued_task(self, task: QueuedTask) -> bool:
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                """
                UPDATE tasks SET status = 'cancelled', completed_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (self._now(), task.id),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def request_task_cancellation(self, task: QueuedTask) -> bool:
        """Persist that a running task must be interrupted before it finishes."""
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                """
                UPDATE tasks SET status = 'cancelling'
                WHERE id = ? AND status = 'running'
                """,
                (task.id,),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def set_thread_id(self, session: Session, thread_id: str) -> Session:
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                UPDATE sessions
                SET codex_thread_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (thread_id, self._now(), session.id),
            )
            await connection.commit()
        return Session(
            id=session.id,
            telegram_user_id=session.telegram_user_id,
            workspace=session.workspace,
            codex_thread_id=thread_id,
        )

    async def clear_thread_id(self, session: Session) -> Session:
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                UPDATE sessions
                SET codex_thread_id = NULL, updated_at = ?
                WHERE id = ?
                """,
                (self._now(), session.id),
            )
            await connection.commit()
        return Session(
            id=session.id,
            telegram_user_id=session.telegram_user_id,
            workspace=session.workspace,
            codex_thread_id=None,
        )

    async def record_turn(
        self,
        session: Session,
        prompt: str,
        *,
        status: str,
        codex_turn_id: str | None = None,
        result: str | None = None,
    ) -> None:
        now = self._now()
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                INSERT INTO turns (
                    id, session_id, codex_turn_id, prompt, result, status, created_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), session.id, codex_turn_id, prompt, result, status, now, now),
            )
            await connection.commit()

    async def delete(self, telegram_user_id: int, workspace: Path) -> None:
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                "DELETE FROM sessions WHERE telegram_user_id = ? AND workspace = ?",
                (telegram_user_id, str(workspace)),
            )
            await connection.commit()

    async def create_approval(self, approval_id: str, session: Session, summary: str) -> None:
        async with aiosqlite.connect(self._database_path) as connection:
            await connection.execute(
                """
                INSERT INTO approvals (
                    id, session_id, telegram_user_id, summary, status, created_at
                )
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (approval_id, session.id, session.telegram_user_id, summary, self._now()),
            )
            await connection.commit()

    async def resolve_approval(self, approval_id: str, telegram_user_id: int, status: str) -> bool:
        async with aiosqlite.connect(self._database_path) as connection:
            cursor = await connection.execute(
                """
                UPDATE approvals SET status = ?, resolved_at = ?
                WHERE id = ? AND telegram_user_id = ? AND status = 'pending'
                """,
                (status, self._now(), approval_id, telegram_user_id),
            )
            await connection.commit()
        return cursor.rowcount == 1

    @staticmethod
    async def _find_session(
        connection: aiosqlite.Connection, telegram_user_id: int, workspace: Path
    ) -> aiosqlite.Row | None:
        cursor = await connection.execute(
            """
            SELECT id, telegram_user_id, workspace, codex_thread_id
            FROM sessions
            WHERE telegram_user_id = ? AND workspace = ?
            """,
            (telegram_user_id, str(workspace)),
        )
        return await cursor.fetchone()

    @staticmethod
    def _session_from_row(row: aiosqlite.Row) -> Session:
        return Session(
            id=row["id"],
            telegram_user_id=row["telegram_user_id"],
            workspace=Path(row["workspace"]),
            codex_thread_id=row["codex_thread_id"],
        )

    @staticmethod
    def _task_from_row(row: aiosqlite.Row, *, status: TaskStatus) -> QueuedTask:
        return QueuedTask(
            id=row["id"],
            telegram_user_id=row["telegram_user_id"],
            workspace=Path(row["workspace"]),
            prompt=row["prompt"],
            status=status,
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
