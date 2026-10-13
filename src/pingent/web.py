"""Optional local HTTP server and browser UI for Pingent."""

import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pingent.config import Settings
from pingent.errors import TaskNotFoundError, WorkspaceNotFoundError
from pingent.markdown import render_web_markdown
from pingent.models import QueuedTask, TaskHistoryEntry, WebSession
from pingent.service import PingentService
from pingent.storage import SessionRepository

ASSETS_DIRECTORY = Path(__file__).with_name("web_assets")


class NoCacheStaticFiles(StaticFiles):
    """Ensure a restarted local server serves the current SPA bundle."""

    async def get_response(self, path: str, scope: dict[str, object]) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class LoginRequest(BaseModel):
    username: str
    password: str


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)


class WorkspaceRequest(BaseModel):
    number: int = Field(ge=1)


def _task_payload(task: QueuedTask) -> dict[str, str]:
    return {
        "id": task.id,
        "workspace": str(task.workspace),
        "prompt": task.prompt,
        "status": task.status,
    }


def _history_payload(entry: TaskHistoryEntry) -> dict[str, str | None]:
    return {
        "id": entry.id,
        "workspace": str(entry.workspace),
        "prompt": entry.prompt,
        "status": entry.status,
        "result": entry.result,
        "result_html": render_web_markdown(entry.result) if entry.result is not None else None,
        "created_at": entry.created_at,
        "completed_at": entry.completed_at,
    }


def create_web_app(
    repository: SessionRepository | None = None,
    service: PingentService | None = None,
    *,
    secure_cookies: bool = False,
) -> FastAPI:
    app = FastAPI(title="Pingent", docs_url=None, redoc_url=None, openapi_url=None)
    login_attempts: dict[str, deque[float]] = defaultdict(deque)

    async def current_session(pingent_session: str | None = Cookie(default=None)) -> WebSession:
        if repository is None or pingent_session is None:
            raise HTTPException(401, "Not authenticated")
        session = await repository.get_web_session(pingent_session)
        if session is None:
            raise HTTPException(401, "Not authenticated")
        return session

    async def csrf_session(
        session: Annotated[WebSession, Depends(current_session)],
        x_csrf_token: str | None = Header(default=None),
    ) -> WebSession:
        if x_csrf_token != session.csrf_token:
            raise HTTPException(403, "Invalid CSRF token")
        return session

    def require_service() -> PingentService:
        if service is None:
            raise HTTPException(503, "Web application is not configured")
        return service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/login")
    async def login(body: LoginRequest, response: Response) -> dict[str, str]:
        if repository is None:
            raise HTTPException(503, "Web authentication is not configured")
        now = time.monotonic()
        attempts = login_attempts[body.username.casefold()]
        while attempts and attempts[0] <= now - 900:
            attempts.popleft()
        if len(attempts) >= 5:
            raise HTTPException(429, "Too many login attempts")
        user = await repository.authenticate_web_user(body.username, body.password)
        if user is None:
            attempts.append(now)
            await repository.record_audit_event(
                "web_login_failed", metadata={"username": body.username}
            )
            raise HTTPException(401, "Invalid credentials")
        if service is not None:
            service.register_web_actor(user.actor_id)
        session = await repository.create_web_session(user)
        response.set_cookie(
            "pingent_session",
            session.token,
            httponly=True,
            samesite="strict",
            secure=secure_cookies,
        )
        await repository.record_audit_event(
            "web_login_succeeded",
            telegram_user_id=user.actor_id,
            metadata={"username": user.username},
        )
        return {"username": user.username, "csrf_token": session.csrf_token}

    @app.get("/api/auth/me")
    async def me(session: Annotated[WebSession, Depends(current_session)]) -> dict[str, str]:
        return {"username": session.user.username, "csrf_token": session.csrf_token}

    @app.post("/api/auth/logout")
    async def logout(
        response: Response,
        session: Annotated[WebSession, Depends(csrf_session)],
    ) -> dict[str, str]:
        assert repository is not None
        await repository.delete_web_session(session.token)
        response.delete_cookie("pingent_session")
        await repository.record_audit_event(
            "web_logout",
            telegram_user_id=session.user.actor_id,
            metadata={"username": session.user.username},
        )
        return {"status": "ok"}

    @app.get("/api/workspaces")
    async def workspaces(
        session: Annotated[WebSession, Depends(current_session)],
        pingent: Annotated[PingentService, Depends(require_service)],
    ) -> dict[str, object]:
        status = await pingent.status(session.user.actor_id)
        configured = await pingent.list_workspaces(session.user.actor_id)
        return {
            "selected": str(status.workspace),
            "workspaces": [str(item) for item in configured],
        }

    @app.put("/api/workspaces")
    async def select_workspace(
        body: WorkspaceRequest,
        session: Annotated[WebSession, Depends(csrf_session)],
        pingent: Annotated[PingentService, Depends(require_service)],
    ) -> dict[str, str]:
        try:
            workspace = await pingent.select_workspace(session.user.actor_id, body.number)
        except WorkspaceNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        return {"selected": str(workspace)}

    @app.get("/api/state")
    async def state(
        session: Annotated[WebSession, Depends(current_session)],
        pingent: Annotated[PingentService, Depends(require_service)],
    ) -> dict[str, object]:
        status = await pingent.status(session.user.actor_id)
        return {
            "workspace": str(status.workspace),
            "session_state": status.state,
            "tasks": [
                _task_payload(task) for task in await pingent.pending_tasks(session.user.actor_id)
            ],
        }

    @app.post("/api/tasks", status_code=201)
    async def create_task(
        body: TaskRequest,
        session: Annotated[WebSession, Depends(csrf_session)],
        pingent: Annotated[PingentService, Depends(require_service)],
    ) -> dict[str, object]:
        prompt = body.prompt.strip()
        if not prompt:
            raise HTTPException(422, "Task prompt must not be empty")
        task, position = await pingent.enqueue_task(session.user.actor_id, prompt)
        return {"task": _task_payload(task), "position": position}

    @app.delete("/api/tasks/{task_id}")
    async def cancel_task(
        task_id: str,
        session: Annotated[WebSession, Depends(csrf_session)],
        pingent: Annotated[PingentService, Depends(require_service)],
    ) -> dict[str, str]:
        try:
            status = await pingent.cancel_task_by_id(session.user.actor_id, task_id)
        except TaskNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        return {"status": status}

    @app.post("/api/session/reset")
    async def reset_session(
        session: Annotated[WebSession, Depends(csrf_session)],
        pingent: Annotated[PingentService, Depends(require_service)],
    ) -> dict[str, str]:
        await pingent.reset_session(session.user.actor_id)
        return {"status": "ok"}

    @app.get("/api/history")
    async def task_history(
        session: Annotated[WebSession, Depends(current_session)],
        pingent: Annotated[PingentService, Depends(require_service)],
    ) -> dict[str, object]:
        return {
            "tasks": [
                _history_payload(task) for task in await pingent.task_history(session.user.actor_id)
            ]
        }

    app.mount("/assets", NoCacheStaticFiles(directory=ASSETS_DIRECTORY), name="assets")

    @app.get("/")
    async def spa() -> FileResponse:
        return FileResponse(ASSETS_DIRECTORY / "index.html")

    return app


def create_web_server(
    settings: Settings,
    repository: SessionRepository | None = None,
    service: PingentService | None = None,
) -> uvicorn.Server:
    config = uvicorn.Config(
        create_web_app(repository, service, secure_cookies=settings.web_secure_cookies),
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
    )
    return uvicorn.Server(config)
