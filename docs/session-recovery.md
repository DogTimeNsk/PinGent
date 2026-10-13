# Codex session recovery

Pingent stores a mapping from a Telegram user and configured workspace to a
Codex `thread.id` in local SQLite. The thread history itself is not stored by
Pingent. It is a persisted Codex rollout owned by the local Codex CLI.

Pingent writes a new `thread.id` to SQLite only after its first turn finishes
successfully. A bare `thread/start` response is not treated as resumable: if
the first turn fails before completion, Pingent leaves no stale link for a
later restart to follow.

On the next Telegram task, Pingent starts a new local `codex app-server`
process and sends `thread/resume` with the stored thread ID. This is the
documented app-server lifecycle. It succeeds only while that CLI installation
can still load the corresponding rollout.

Pingent closes an idle app-server by closing its standard input and waiting for
normal process exit. It uses signals only as a timeout fallback, so Codex has a
chance to flush its local rollout before the next process attempts to resume it.

## When a rollout is unavailable

If app-server rejects `thread/resume` with `no rollout found for thread id`,
Pingent treats the SQLite thread ID as stale. It records the failed turn,
clears only the saved ID, and sends a Telegram explanation. It deliberately
does not silently create a replacement thread because that would lose the
previous Codex context without telling the user.

After that message, resend the task. Pingent creates a new Codex thread and
continues with a fresh context. `/new` is optional: it reaches the same fresh
session state, but can be used proactively when discarding a conversation.

The rollout can be unavailable when its local Codex data has been deleted or
when Pingent starts Codex with a different local CLI state than the process
that created the thread (for example, a different `CODEX_HOME`). Pingent does
not infer a more specific cause from this protocol error.

## Verification and diagnostics

The opt-in integration test creates a thread, completes a harmless text-only
turn, closes the app-server process, starts a new one, and resumes the same
thread. It deletes its test thread on cleanup:

```bash
PINGENT_RUN_CODEX_INTEGRATION=1 uv run pytest -m integration
```

Run this check after upgrading Codex or changing how Pingent is started. If it
fails, keep the output of `codex --version` and `codex doctor`; do not share
Codex authentication files or rollout transcripts. The CLI keeps local
sessions under its own state directory, which depends on the Codex environment.

## Boundaries

Pingent's recovery guarantee is limited to its own SQLite link: stale links are
cleared safely. It cannot restore a deleted or otherwise unavailable Codex
rollout, and it does not attach to an arbitrary pre-existing CLI process.
