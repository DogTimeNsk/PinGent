import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from pingent.config import Settings
from pingent.models import QueuedTask, SessionStatus, TaskHistoryEntry
from pingent.storage import SessionRepository
from pingent.web import create_web_app, create_web_server
from pingent.web_auth import hash_password


class StubWebService:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.tasks: list[QueuedTask] = []
        self.history: tuple[TaskHistoryEntry, ...] = ()

    def register_web_actor(self, actor_id: int) -> None:
        self.actor_id = actor_id

    async def status(self, actor_id: int) -> SessionStatus:
        return SessionStatus(workspace=self.workspace, state="not_started")

    async def list_workspaces(self, actor_id: int) -> tuple[Path, ...]:
        return (self.workspace,)

    async def select_workspace(self, actor_id: int, number: int) -> Path:
        return self.workspace

    async def pending_tasks(self, actor_id: int) -> tuple[QueuedTask, ...]:
        return tuple(self.tasks)

    async def enqueue_task(self, actor_id: int, prompt: str) -> tuple[QueuedTask, int]:
        task = QueuedTask("task-1", actor_id, self.workspace, prompt, "queued")
        self.tasks.append(task)
        return task, len(self.tasks)

    async def cancel_task_by_id(self, actor_id: int, task_id: str) -> str:
        self.tasks = [task for task in self.tasks if task.id != task_id]
        return "cancelled"

    async def reset_session(self, actor_id: int) -> None:
        return None

    async def task_history(self, actor_id: int) -> tuple[object, ...]:
        return self.history


def test_web_app_exposes_health_and_auth_routes() -> None:
    assert {route.path for route in create_web_app().routes} >= {
        "/",
        "/assets",
        "/health",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/workspaces",
        "/api/state",
        "/api/tasks",
        "/api/tasks/{task_id}",
        "/api/session/reset",
        "/api/history",
    }


def test_web_spa_renders_cancelling_tasks_in_a_separate_stopping_group() -> None:
    with TestClient(create_web_app()) as client:
        javascript = client.get("/assets/app.js").text

    assert 'task.status === "cancelling"' in javascript
    assert 'h3>Stopping</h3>' in javascript
    assert 'active && !stopping' in javascript


def test_web_server_uses_the_configured_loopback_bind(tmp_path: Path) -> None:
    settings = Settings(
        telegram_bot_token="token",
        allowed_telegram_user_ids={42},
        workspaces=[tmp_path],
        web_enabled=True,
        web_host="127.0.0.1",
        web_port=8090,
    )

    server = create_web_server(settings)

    assert server.config.host == "127.0.0.1"
    assert server.config.port == 8090


def test_web_spa_authenticates_and_uses_csrf_protected_task_api(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "pingent.sqlite3")
    asyncio.run(repository.initialize())
    asyncio.run(repository.create_web_user("alice", hash_password("a sufficiently long password")))
    service = StubWebService(tmp_path)

    with TestClient(create_web_app(repository, service)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/assets/app.js").headers["cache-control"] == "no-cache"
        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "a sufficiently long password"},
        )
        assert login.status_code == 200
        csrf_token = login.json()["csrf_token"]
        assert client.get("/api/workspaces").json()["selected"] == str(tmp_path)
        assert client.post("/api/tasks", json={"prompt": "Inspect this project"}).status_code == 403
        created = client.post(
            "/api/tasks",
            json={"prompt": "Inspect this project"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert created.status_code == 201
        assert client.get("/api/state").json()["tasks"][0]["prompt"] == "Inspect this project"
        assert client.delete(
            "/api/tasks/task-1", headers={"X-CSRF-Token": csrf_token}
        ).json() == {"status": "cancelled"}


def test_history_includes_server_rendered_markdown(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "pingent.sqlite3")
    asyncio.run(repository.initialize())
    asyncio.run(repository.create_web_user("alice", hash_password("a sufficiently long password")))
    service = StubWebService(tmp_path)
    service.history = (
        TaskHistoryEntry(
            id="turn-1",
            workspace=tmp_path,
            prompt="Send Markdown",
            status="completed",
            result="```python\nprint('ok')\n```",
            created_at="2026-10-12T00:00:00+00:00",
            completed_at="2026-10-12T00:00:01+00:00",
        ),
    )

    with TestClient(create_web_app(repository, service)) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "a sufficiently long password"},
        )
        assert login.status_code == 200
        task = client.get("/api/history").json()["tasks"][0]

    assert task["result_html"] == (
        '<pre><code class="language-python">print(\'ok\')\n</code></pre>\n'
    )
