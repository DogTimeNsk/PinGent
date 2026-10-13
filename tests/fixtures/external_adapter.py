import json
import sys


def send(message: dict[str, object]) -> None:
    print(json.dumps(message), flush=True)


send({"type": "hello", "protocol_version": 1})
initialize = json.loads(sys.stdin.readline())
assert initialize["type"] == "initialize"
send({"type": "incoming_message", "text": "task from external adapter"})

for line in sys.stdin:
    if json.loads(line).get("type") == "shutdown":
        break
