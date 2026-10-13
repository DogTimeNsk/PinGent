import asyncio

import pytest

from pingent.codex import CodexAppServer
from pingent.errors import CodexError


class FakeStdin:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


@pytest.mark.asyncio
async def test_close_allows_app_server_to_exit_cleanly_before_terminating(tmp_path) -> None:
    agent = CodexAppServer(tmp_path, "codex")
    process = FakeProcess()
    agent._process = process  # type: ignore[assignment]

    await agent.close()

    assert process.stdin.closed
    assert not process.terminated
    assert not process.killed


@pytest.mark.asyncio
async def test_new_thread_uses_configured_model(monkeypatch, tmp_path) -> None:
    agent = CodexAppServer(tmp_path, "codex", model="gpt-5.3-codex")
    requests: list[tuple[str, dict[str, object]]] = []

    async def request(
        method: str, params: dict[str, object], **_: object
    ) -> dict[str, object]:
        requests.append((method, params))
        return {"thread": {"id": "thread-1"}}

    monkeypatch.setattr(agent, "_request", request)

    assert await agent.create_thread() == "thread-1"
    assert requests == [("thread/start", {"cwd": str(tmp_path), "model": "gpt-5.3-codex"})]


@pytest.mark.asyncio
async def test_cancel_turn_interrupts_the_active_codex_turn(monkeypatch, tmp_path) -> None:
    agent = CodexAppServer(tmp_path, "codex")
    agent._active_thread_id = "thread-1"
    agent._active_turn_id = "turn-1"
    requests: list[tuple[str, dict[str, object]]] = []

    async def request(
        method: str, params: dict[str, object], **_: object
    ) -> dict[str, object]:
        requests.append((method, params))
        return {}

    monkeypatch.setattr(agent, "_request", request)

    await agent.cancel_turn()

    assert requests == [("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"})]


@pytest.mark.asyncio
async def test_turn_uses_the_cli_approval_policy_spelling(monkeypatch, tmp_path) -> None:
    agent = CodexAppServer(tmp_path, "codex")
    requests: list[tuple[str, dict[str, object]]] = []

    async def request(method: str, params: dict[str, object]) -> dict[str, object]:
        requests.append((method, params))
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(f"Unexpected request: {method}")

    monkeypatch.setattr(agent, "_request", request)
    await agent._events.put(
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-1", "status": "completed"}},
        }
    )

    await agent.run_turn("thread-1", "Inspect the workspace", lambda _: "decline")

    assert requests[0][0] == "turn/start"
    assert requests[0][1]["approvalPolicy"] == "on-request"


@pytest.mark.asyncio
async def test_turn_denies_additional_permissions_for_its_current_turn(
    monkeypatch, tmp_path
) -> None:
    agent = CodexAppServer(tmp_path, "codex")
    writes: list[dict[str, object]] = []

    async def request(method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "turn/start"
        return {"turn": {"id": "turn-1"}}

    async def write(message: dict[str, object]) -> None:
        writes.append(message)

    monkeypatch.setattr(agent, "_request", request)
    monkeypatch.setattr(agent, "_write", write)
    await agent._events.put(
        {
            "method": "item/permissions/requestApproval",
            "id": 98,
            "params": {
                "permissions": {"fileSystem": {"write": ["/tmp"]}},
                "reason": "Write outside the workspace",
            },
        }
    )
    await agent._events.put(
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-1", "status": "completed"}},
        }
    )

    await agent.run_turn("thread-1", "Write a temporary file", lambda _: "decline")

    assert writes == [{"id": 98, "result": {"permissions": {}, "scope": "turn"}}]


@pytest.mark.asyncio
async def test_turn_fails_when_app_server_closes_while_waiting_for_completion(
    monkeypatch, tmp_path
) -> None:
    agent = CodexAppServer(tmp_path, "codex")

    async def request(method: str, params: dict[str, object], **_: object) -> dict[str, object]:
        assert method == "turn/start"
        return {"turn": {"id": "turn-1"}}

    monkeypatch.setattr(agent, "_request", request)
    turn = asyncio.create_task(agent.run_turn("thread-1", "Inspect", lambda _: "decline"))
    await asyncio.sleep(0)
    agent._fail(CodexError("Codex app-server closed its output stream"))

    with pytest.raises(CodexError, match="closed its output stream"):
        await turn


@pytest.mark.asyncio
async def test_request_times_out_and_removes_its_pending_response(monkeypatch, tmp_path) -> None:
    agent = CodexAppServer(tmp_path, "codex")

    async def write(message: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(agent, "_write", write)

    with pytest.raises(CodexError, match="timed out while handling thread/resume"):
        await agent._request("thread/resume", {"threadId": "thread-1"}, timeout=0.01)

    assert not agent._pending


@pytest.mark.asyncio
async def test_request_uses_the_configured_default_timeout(monkeypatch, tmp_path) -> None:
    agent = CodexAppServer(tmp_path, "codex", request_timeout_seconds=0.01)

    async def write(message: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(agent, "_write", write)

    with pytest.raises(CodexError, match="timed out while handling thread/resume"):
        await agent._request("thread/resume", {"threadId": "thread-1"})


@pytest.mark.asyncio
async def test_request_timeout_override_takes_precedence(monkeypatch, tmp_path) -> None:
    agent = CodexAppServer(tmp_path, "codex", request_timeout_seconds=0.01)

    async def write(message: dict[str, object]) -> None:
        async def respond() -> None:
            await asyncio.sleep(0.02)
            agent._pending[message["id"]].set_result({"result": {}})  # type: ignore[index]

        asyncio.create_task(respond())

    monkeypatch.setattr(agent, "_write", write)
    assert (
        await agent._request(
            "thread/resume",
            {"threadId": "thread-1"},
            timeout=0.1,
        )
        == {}
    )
