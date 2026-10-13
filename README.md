# Pingent
1;5B
Pingent is a self-hosted gateway for two-way interaction with AI coding agents.
Each installation runs independently on the operator's Linux computer and owns
its Telegram bot, local SQLite database, workspaces, and Codex sessions.

Pingent is not a centralized service and has no mandatory Pingent account,
shared control plane, or tenant model.

## Status

The first local vertical slice is implemented: an allowlisted Telegram user can
send a text task to a configured workspace, Pingent runs local Codex, then
returns the final answer to Telegram. Pingent stores the Codex thread ID in
local SQLite and reuses it for the next task from that user.

Telegram approval buttons are available for command and file-change requests.
Permission requests are still declined safely. Multiple workspace selection,
durable task queues, recovery of interrupted turns, task status, and
cancellation are implemented. Rich streaming progress updates are not
implemented yet.

SQLite schema changes are applied locally through versioned migrations. Pingent
also keeps a local audit history for security and lifecycle events, including
authorization denials, workspace changes, task lifecycle transitions, session
resets, and approval decisions. Audit metadata deliberately excludes task
prompts and Codex responses.

If Codex no longer has a stored thread referenced by Pingent, Pingent clears
only that stale link and asks the user to resend the task. The new task starts
a fresh Codex context; it does not silently pretend to restore the old one.
See [Codex session recovery](docs/session-recovery.md) for the verified
lifecycle, diagnostics, and recovery procedure.

## Development setup

Pingent uses [uv](https://docs.astral.sh/uv/) for dependency and environment
management. Install uv, then run:

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Python 3.11 or newer is required. The project pins Python 3.11 as its default
development version in `.python-version`.

The default test suite includes isolated integration tests for the Telegram
adapter and Codex JSON-RPC transport; they use no external Telegram or Codex
service. The separate opt-in test with a real local Codex CLI is documented in
[Codex CLI compatibility](docs/codex-compatibility.md).

## Configuration

Copy `.env.example` to `.env` before configuring a local installation. Never
commit `.env` or bot tokens.

`PINGENT_ALLOWED_TELEGRAM_USER_IDS` and `PINGENT_WORKSPACES` are JSON arrays.
The first configured workspace is the default for a Telegram user. Use
`/workspaces` to list configured paths and `/workspace <number>` to select one.
Pingent stores that selection locally and keeps a separate Codex session for
each user/workspace pair. `/new` resets only the session of the selected
workspace. Use `/help` in Telegram to see every available control command and
its concise description.

Set `PINGENT_LOCALE` to `en` (default) or `ru`. The fixed Telegram interface
messages live in the editable `src/pingent/locales.py` dictionaries. Set
`PINGENT_CODEX_MODEL` to pass a model override to newly created local Codex
threads; leave it empty to use Codex's default model. Set
`PINGENT_CODEX_REQUEST_TIMEOUT_SECONDS` (default: `30`) to control how long
Pingent waits for a normal Codex app-server JSON-RPC response, including
session resume; it must be positive. `/status` reports the
local workspace and whether the caller has a stored Codex session, while
`/model` reports this local model configuration. `/status` shows the configured
workspace, not a transient `cd` performed by a command during a completed
Codex turn: Pingent starts every local Codex process in the configured workspace.

Pingent always denies Codex `request_permissions` requests with an empty,
turn-scoped permission profile. It never grants additional filesystem or network
access through that protocol. Command and file-change requests remain subject to
the one-time Telegram approval controls.

Messages sent while another task for the same user/workspace is running are
stored in local SQLite and processed in order. On restart, queued tasks resume;
a task that was already running is marked interrupted and is not retried
automatically because it may have partially changed local files.
Use `/tasks` to view the active task and queue for the selected workspace.
Use `/cancel <number>` to remove a queued task or interrupt the active one.

Codex CLI must be installed and authenticated locally before starting Pingent.
For example, complete `codex login`, then run:

```bash
uv run python -m pingent
```

See [Codex CLI compatibility](docs/codex-compatibility.md) for the supported
CLI baseline and the opt-in integration check to run before a CLI upgrade.

## Web interface

The optional Web SPA is disabled by default. Create a local account without
putting its password into shell history:

```bash
uv run pingent web-user create alice
```

Set `PINGENT_WEB_ENABLED=true` and keep the default
`PINGENT_WEB_HOST=127.0.0.1`, then open `http://127.0.0.1:8080`. The browser
can select a configured workspace, submit and cancel tasks, reset its own
Codex session, and view the current queue and its recent task history. The UI
polls every three seconds; live streaming is intentionally out of scope. Final
Codex responses are rendered as safe CommonMark in the Web SPA and, within
Telegram's one-message limit, in Telegram too; this includes fenced code blocks.

Do not expose the server directly to a network without TLS. A non-loopback
bind requires both `PINGENT_WEB_ALLOW_REMOTE=true` and
`PINGENT_WEB_SECURE_COOKIES=true`; terminate HTTPS at a local reverse proxy and
make the browser use its HTTPS URL. Pingent itself does not manage certificates.

## Architecture

See [Architecture](docs/architecture.md) for Local Mode boundaries, the
self-hosted deployment model, security rules, and deliberate MVP exclusions.

## External adapters

Pingent can optionally supervise a trusted local channel adapter using a small,
versioned JSON Lines protocol. The included [PHP console example](docs/adapter-extension.md)
shows how an adapter can be written without making PHP a Pingent dependency.

## License

Pingent is licensed under the GNU General Public License v3.0.
See [LICENSE](LICENSE) for details.
