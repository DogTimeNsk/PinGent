"""Minimal JSON-RPC client for a local Codex app-server subprocess."""

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pingent.errors import (
    ApprovalRequiredError,
    CodexError,
    CodexSessionUnavailableError,
    CodexTurnInterruptedError,
)
from pingent.models import AgentApproval, ApprovalHandler, TurnResult

logger = logging.getLogger(__name__)

INTERRUPT_TIMEOUT_SECONDS = 5


class CodexAppServer:
    """Own one local app-server process for one Pingent task."""

    def __init__(
        self,
        workspace: Path,
        codex_binary: str,
        model: str | None = None,
        request_timeout_seconds: float = 30,
    ) -> None:
        self._workspace = workspace
        self._codex_binary = codex_binary
        self._model = model
        self._request_timeout_seconds = request_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._events: asyncio.Queue[dict[str, Any] | CodexError] = asyncio.Queue()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._request_id = 0
        self._active_thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._cancel_requested = False
        self._terminal_error: CodexError | None = None

    async def start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._codex_binary,
                "app-server",
                cwd=self._workspace,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise CodexError(f"Codex binary was not found: {self._codex_binary}") from error

        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        await self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "pingent",
                    "title": "Pingent",
                    "version": "0.1.0",
                }
            },
        )
        await self._notify("initialized", {})

    async def close(self) -> None:
        background_tasks = [
            task for task in (self._reader_task, self._stderr_task) if task is not None
        ]

        if self._process is not None and self._process.returncode is None:
            if self._process.stdin is not None:
                self._process.stdin.close()
                try:
                    await self._process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5)
                except TimeoutError:
                    self._process.kill()
                    await self._process.wait()

        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    async def create_thread(self) -> str:
        params: dict[str, Any] = {"cwd": str(self._workspace)}
        if self._model is not None:
            params["model"] = self._model
        response = await self._request("thread/start", params)
        thread_id = response.get("thread", {}).get("id")
        if not isinstance(thread_id, str):
            raise CodexError("Codex did not return a thread ID")
        return thread_id

    async def resume_thread(self, thread_id: str) -> None:
        try:
            await self._request(
                "thread/resume", {"threadId": thread_id, "cwd": str(self._workspace)}
            )
        except CodexError as error:
            if "no rollout found for thread id" in str(error).lower():
                raise CodexSessionUnavailableError(
                    "The saved Codex thread is unavailable on this computer"
                ) from error
            raise

    async def cancel_turn(self) -> None:
        self._cancel_requested = True
        if self._active_thread_id is not None and self._active_turn_id is not None:
            try:
                await self._request(
                    "turn/interrupt",
                    {"threadId": self._active_thread_id, "turnId": self._active_turn_id},
                    timeout=INTERRUPT_TIMEOUT_SECONDS,
                )
            except CodexError as cause:
                # A local subprocess that cannot acknowledge an interrupt must not keep a
                # web task in "cancelling" forever.  Closing it also releases run_turn().
                error = CodexError("Codex did not acknowledge the turn interruption")
                self._fail(error)
                if self._process is not None and self._process.returncode is None:
                    self._process.terminate()
                raise error from cause

    async def run_turn(
        self, thread_id: str, prompt: str, approval_handler: ApprovalHandler
    ) -> TurnResult:
        response = await self._request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "cwd": str(self._workspace),
                "approvalPolicy": "on-request",
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "writableRoots": [str(self._workspace)],
                    "networkAccess": False,
                },
            },
        )
        turn_id = response.get("turn", {}).get("id")
        if not isinstance(turn_id, str):
            raise CodexError("Codex did not return a turn ID")
        self._active_thread_id = thread_id
        self._active_turn_id = turn_id
        if self._cancel_requested:
            await self.cancel_turn()

        final_messages: list[str] = []
        while True:
            event = await self._events.get()
            if isinstance(event, CodexError):
                raise event
            method = event.get("method")
            params = event.get("params", {})

            if method in {
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            }:
                request_id = event.get("id")
                if not isinstance(request_id, (int, str)):
                    raise CodexError("Codex approval request did not contain an ID")
                decision = await approval_handler(
                    AgentApproval(request_id, self._approval_summary(method, params))
                )
                await self._write({"id": request_id, "result": {"decision": decision}})
                continue

            if method == "item/permissions/requestApproval":
                request_id = event.get("id")
                if not isinstance(request_id, (int, str)):
                    raise CodexError("Codex permission request did not contain an ID")
                await self._write(
                    {"id": request_id, "result": {"permissions": {}, "scope": "turn"}}
                )
                logger.info("Denied Codex request for additional permissions")
                continue

            if method == "tool/requestUserInput":
                raise ApprovalRequiredError("Codex requested user approval")

            if method == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                    final_messages.append(item["text"])

            if method == "turn/completed" and params.get("turn", {}).get("id") == turn_id:
                turn = params["turn"]
                if turn.get("status") == "interrupted":
                    raise CodexTurnInterruptedError("Codex turn was interrupted")
                if turn.get("status") != "completed":
                    message = turn.get("error", {}).get("message", "Codex turn did not complete")
                    raise CodexError(str(message))
                text = "\n\n".join(dict.fromkeys(final_messages)).strip()
                return TurnResult(
                    text=text or "Codex completed without a final message.",
                    codex_turn_id=turn_id,
                )

    async def _request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._terminal_error is not None:
            raise self._terminal_error
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        response: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = response
        try:
            await self._write({"method": method, "id": request_id, "params": params})
            try:
                message = await asyncio.wait_for(
                    response,
                    timeout=(self._request_timeout_seconds if timeout is None else timeout),
                )
            except TimeoutError as error:
                raise CodexError(f"Codex timed out while handling {method}") from error
        finally:
            self._pending.pop(request_id, None)
        if "error" in message:
            raise CodexError(str(message["error"].get("message", "Codex protocol error")))
        result = message.get("result")
        if not isinstance(result, dict):
            raise CodexError(f"Codex returned an invalid response to {method}")
        return result

    async def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        await self._write({"method": method, "params": params})

    async def _write(self, message: Mapping[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise CodexError("Codex app-server is not running")
        self._process.stdin.write(json.dumps(message).encode() + b"\n")
        await self._process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        while line := await self._process.stdout.readline():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ignoring non-JSON output from Codex app-server")
                continue

            request_id = message.get("id")
            if isinstance(request_id, int) and ("result" in message or "error" in message):
                future = self._pending.pop(request_id, None)
                if future is not None and not future.done():
                    future.set_result(message)
                continue
            await self._events.put(message)

        self._fail(CodexError("Codex app-server closed its output stream"))

    def _fail(self, error: CodexError) -> None:
        if self._terminal_error is not None:
            return
        self._terminal_error = error
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._events.put_nowait(error)

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        while await self._process.stderr.readline():
            logger.debug("Codex app-server wrote a diagnostic line to stderr")

    @staticmethod
    def _approval_summary(method: str, params: Mapping[str, Any]) -> str:
        reason = params.get("reason") or "n/a"
        if method == "item/commandExecution/requestApproval":
            return f"Command: {params.get('command', 'unavailable')}\nReason: {reason}"
        return f"File change requested\nReason: {reason}"
