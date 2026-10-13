"""Local JSON Lines host for out-of-process channel adapters."""

import asyncio
import json
import logging
import os
from collections.abc import Iterable

from pingent.config import ExternalAdapterSettings
from pingent.models import TaskEvent
from pingent.service import PingentService

logger = logging.getLogger(__name__)
PROTOCOL_VERSION = 1


class ExternalAdapterHost:
    """Supervise trusted local adapter processes without exposing Core internals."""

    def __init__(
        self, adapters: Iterable[ExternalAdapterSettings], service: PingentService
    ) -> None:
        self._adapters = {adapter.actor_id: adapter for adapter in adapters}
        self._service = service
        self._processes: dict[int, asyncio.subprocess.Process] = {}
        self._writers: dict[int, asyncio.Lock] = {}
        self._readers: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        for actor_id, adapter in self._adapters.items():
            process = await asyncio.create_subprocess_exec(
                *adapter.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": os.defpath, "LANG": os.environ.get("LANG", "C.UTF-8")},
            )
            assert process.stdin is not None and process.stdout is not None
            self._processes[actor_id] = process
            self._writers[actor_id] = asyncio.Lock()
            hello = await asyncio.wait_for(process.stdout.readline(), timeout=5)
            if not self._valid_hello(hello):
                process.terminate()
                await process.wait()
                raise RuntimeError(
                    f"External adapter for actor {actor_id} did not send a valid hello"
                )
            await self._send(
                actor_id,
                {
                    "type": "initialize",
                    "protocol_version": PROTOCOL_VERSION,
                    "actor_id": actor_id,
                },
            )
            self._readers.append(asyncio.create_task(self._read_adapter(actor_id, process)))

    async def close(self) -> None:
        for actor_id in tuple(self._processes):
            await self._send(actor_id, {"type": "shutdown"})
        for process in self._processes.values():
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.terminate()
                await process.wait()
        for reader in self._readers:
            await reader
        self._processes.clear()

    async def notify_task(self, event: TaskEvent) -> None:
        actor_id = event.task.telegram_user_id
        if actor_id not in self._processes:
            return
        await self._send(
            actor_id,
            {
                "type": "task",
                "task_id": event.task.id,
                "status": event.status,
                "result": event.result,
            },
        )

    async def _read_adapter(self, actor_id: int, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        async for line in process.stdout:
            try:
                message = json.loads(line)
                if message.get("type") != "incoming_message" or not isinstance(
                    message.get("text"), str
                ):
                    raise ValueError("unsupported message")
                text = message["text"].strip()
                if not text:
                    raise ValueError("empty task")
                task, position = await self._service.enqueue_task(actor_id, text)
                await self._send(
                    actor_id,
                    {"type": "queued", "task_id": task.id, "position": position},
                )
            except (ValueError, json.JSONDecodeError):
                logger.warning("Ignoring invalid message from external adapter actor %s", actor_id)
            except Exception:
                logger.exception("External adapter message handling failed for actor %s", actor_id)

    async def _send(self, actor_id: int, message: dict[str, object]) -> None:
        process = self._processes.get(actor_id)
        if process is None or process.stdin is None or process.returncode is not None:
            return
        async with self._writers[actor_id]:
            process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
            await process.stdin.drain()

    @staticmethod
    def _valid_hello(line: bytes) -> bool:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return False
        return message == {"type": "hello", "protocol_version": PROTOCOL_VERSION}
