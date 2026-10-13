from contextlib import suppress

import pytest

from pingent.codex import CodexAppServer
from pingent.errors import CodexError


@pytest.mark.integration
@pytest.mark.asyncio
async def test_supported_codex_app_server_initializes(codex_binary: str, tmp_path) -> None:
    """Exercise Pingent's real JSON-RPC initialization sequence without a turn."""
    agent = CodexAppServer(tmp_path, codex_binary)

    try:
        await agent.start()
    finally:
        await agent.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_thread_resumes_after_the_app_server_restarts(codex_binary: str, tmp_path) -> None:
    """Verify the persisted rollout lifecycle used by Pingent between tasks."""
    first_agent = CodexAppServer(tmp_path, codex_binary)
    await first_agent.start()
    try:
        thread_id = await first_agent.create_thread()
        await first_agent.run_turn(
            thread_id,
            "Reply with exactly: Pingent integration check complete. Do not use tools.",
            lambda _: "decline",
        )
    finally:
        await first_agent.close()

    resumed_agent = CodexAppServer(tmp_path, codex_binary)
    await resumed_agent.start()
    try:
        await resumed_agent.resume_thread(thread_id)
    finally:
        with suppress(CodexError):
            await resumed_agent._request("thread/delete", {"threadId": thread_id})
        await resumed_agent.close()
