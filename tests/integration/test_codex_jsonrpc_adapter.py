import json
import sys
from pathlib import Path

import pytest

from pingent.codex import CodexAppServer


def _write_fake_codex(binary: Path, request_log: Path) -> None:
    binary.write_text(
        f"""#!{sys.executable}
import json
import sys

request_log = {str(request_log)!r}

def send(message):
    sys.stdout.write(json.dumps(message) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    message = json.loads(line)
    with open(request_log, "a", encoding="utf-8") as output:
        output.write(json.dumps(message) + "\\n")
    if message.get("method") == "initialize":
        send({{"id": message["id"], "result": {{}}}})
    elif message.get("method") == "thread/start":
        send({{"id": message["id"], "result": {{"thread": {{"id": "thread-1"}}}}}})
    elif message.get("method") == "thread/resume":
        send({{"id": message["id"], "result": {{}}}})
    elif message.get("method") == "turn/start":
        send({{"id": message["id"], "result": {{"turn": {{"id": "turn-1"}}}}}})
        send({{
            "method": "item/commandExecution/requestApproval",
            "id": 50,
            "params": {{"command": "git status", "reason": "Inspect changes"}},
        }})
    elif message.get("id") == 50:
        send({{
            "method": "item/completed",
            "params": {{"item": {{"type": "agentMessage", "text": "Working tree is clean."}}}},
        }})
        send({{
            "method": "turn/completed",
            "params": {{"turn": {{"id": "turn-1", "status": "completed"}}}},
        }})
"""
    )
    binary.chmod(0o755)


@pytest.mark.asyncio
async def test_json_rpc_adapter_exchanges_requests_events_and_approvals(tmp_path: Path) -> None:
    request_log = tmp_path / "requests.jsonl"
    fake_codex = tmp_path / "fake-codex"
    _write_fake_codex(fake_codex, request_log)
    agent = CodexAppServer(tmp_path, str(fake_codex))
    approvals = []

    async def decline(approval):
        approvals.append(approval)
        return "decline"

    try:
        await agent.start()
        thread_id = await agent.create_thread()
        await agent.resume_thread(thread_id)
        result = await agent.run_turn(
            thread_id,
            "Inspect the repository",
            decline,
        )
    finally:
        await agent.close()

    requests = [json.loads(line) for line in request_log.read_text().splitlines()]
    turn_request = next(request for request in requests if request.get("method") == "turn/start")
    approval_response = next(request for request in requests if request.get("id") == 50)

    assert thread_id == "thread-1"
    assert result.text == "Working tree is clean."
    assert result.codex_turn_id == "turn-1"
    assert approvals[0].summary == "Command: git status\nReason: Inspect changes"
    assert turn_request["params"]["approvalPolicy"] == "on-request"
    assert turn_request["params"]["sandboxPolicy"]["networkAccess"] is False
    assert approval_response == {"id": 50, "result": {"decision": "decline"}}
