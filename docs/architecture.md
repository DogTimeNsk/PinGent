# Architecture

## Scope and deployment model

Pingent is a self-hosted application. Every installation runs independently on the operator's own Linux computer and is a complete trust boundary.

An installation has its own:

- Telegram bot and bot token;
- configuration and allowed Telegram user IDs;
- local SQLite database;
- configured local workspaces;
- local Codex processes and persisted Codex threads.

Pingent does not require, contact, or depend on a Pingent-operated central service. It has no global account system, tenant registry, shared database, mandatory registration, or centralized authorization. External communication is limited to the APIs of enabled channels, such as Telegram, and services required by the configured AI agent, such as OpenAI services used by Codex.

Modularity serves one installation: it permits adding a channel adapter or agent adapter without coupling the Core to Telegram or Codex. It is not a foundation for a multi-tenant SaaS.

## Local Mode

The MVP runs these components on one computer:

```text
Authorized Telegram user
        |
        v
Telegram adapter <--> Core <--> SQLite
                       |
                       v
                 Codex adapter
                       |
                       v
             local codex app-server
                       |
                       v
               configured local workspace
```

The Telegram adapter uses long polling, so MVP does not need a public HTTP endpoint. The Codex adapter starts and supervises local `codex app-server` subprocesses and communicates through JSON-RPC over standard input/output.

Codex app-server is an external integration with an experimental protocol surface. Pingent keeps it behind the Codex adapter, pins and tests a compatible CLI version during bootstrap, and does not let app-server types escape into the Core.

## Component boundaries

### Core

The Core handles local authorization, command routing, sessions, turns, approval state, history, error handling, and notification requests. It is independent of Telegram and Codex.

### Channel adapters

A channel adapter converts external updates into Core commands and Core notifications into channel messages. The first adapter is Telegram. There is one configured bot per installation; a Telegram user ID is an authorization attribute, not a Pingent account or tenant.

An optional external channel adapter is a locally supervised child process. It
uses a versioned JSON Lines protocol over standard input/output and is assigned
a fixed local actor ID by configuration. It receives neither Pingent secrets nor
database access. The current protocol only submits plain tasks and receives task
delivery events; it cannot select arbitrary workspaces, execute shell commands,
or approve Codex requests.

### Agent adapters

An agent adapter converts Core operations into an agent-specific protocol and converts agent events into Core events. The first adapter targets Codex app-server. Codex features must be treated as capability-specific; future agents are not assumed to support equivalent session, event, or approval behavior.

### Local runner

The local runner supervises the Codex subprocess for an active Pingent session. It fixes the process working directory to the session's configured workspace, forwards structured events, captures diagnostics, and performs graceful shutdown. Distributed runners are explicitly out of scope for MVP.

### Storage

SQLite persists Pingent state locally. The initial logical records are:

- sessions, including the owner Telegram ID, workspace, state, and external Codex thread ID;
- turns and their final outcome or failure;
- queued tasks, including their workspace, prompt, lifecycle status, and final result;
- pending and resolved approvals;
- processed Telegram update IDs for idempotency;
- security-relevant audit events.

SQLite schema changes are applied in ordered, recorded migrations. Audit events
contain an event type, timestamp, optional Telegram user ID, and small
structured metadata. They record authorization denials and state-changing
control actions, but never task prompts, Codex responses, or approval summaries.

The local configuration is the source of truth for allowed Telegram IDs and
allowed workspace paths. SQLite stores the currently selected configured
workspace for each Telegram user; it does not create a mutable workspace
registry. Each user/workspace pair owns an independent local Codex session.
A database-backed user directory, dynamic agent registry, or tenant model is
not needed for MVP.

## Session lifecycle

1. An authorized Telegram user starts a session in an allowed local workspace.
2. Pingent starts a local Codex app-server and creates a Codex thread.
3. Pingent stores the returned thread ID in its local SQLite database.
4. A Telegram instruction becomes a durable queued task. One worker processes
   tasks in order for each user/workspace session; events become local state
   changes and selected notifications.
   The owner can inspect its active queue and cancel a queued task or request
   interruption of the active Codex turn.
5. A later instruction resumes the stored Codex thread when necessary.
6. On restart, tasks left running are marked interrupted and are never replayed
   automatically; still-queued tasks resume. Pingent starts a new local
   app-server and attempts to resume
   the stored thread. This depends on the local Codex CLI retaining the
   underlying rollout. If it is unavailable, Pingent clears only its stale link
   and the next task creates a fresh thread. A pending approval is never
   restored as approved.

The MVP processes one active turn per Pingent session. Normal instructions received during a turn are queued; they do not silently alter the in-progress task.

## Security model

- Only explicitly configured Telegram user IDs and locally created Web SPA users may control the installation.
- Sessions remain bound to the Telegram ID that created them.
- Workspaces are selected only from configured paths after canonicalization; user messages cannot choose arbitrary filesystem locations.
- Codex runs with the least practical sandbox permissions. Dangerous actions require an explicit, scoped approval from the session owner.
- Telegram callback actions are single-use, time-limited, and bound to a pending approval record.
- A task interrupted by a restart is never automatically replayed because agent
  actions may have partially modified local files.
- Agent output is data, never a trusted Core command.
- Secrets, token values, `.env` files, and sensitive command output must not be committed or logged.
- Web SPA sessions use opaque, expiring HttpOnly/SameSite cookies and a per-session CSRF token. A remote web bind is opt-in and requires secure cookies and HTTPS terminated by the operator; Pingent does not provide certificate management.
- The Web SPA does not expose Codex approval controls. A permission request from a Web-owned task is recorded and declined automatically.

## Deliberate MVP exclusions

- A Pingent cloud service, shared control plane, or multi-tenant deployment.
- Mandatory Pingent accounts, registration, billing, or centralized identity.
- Distributed runners and remote Pingent Core instances.
- Attachment to an arbitrary already-running Codex terminal UI.
- Slack, Redis, PostgreSQL, message brokers, and container orchestration.

## Technology choices

- Python 3.11 or newer and `asyncio` for the application runtime.
- `aiogram` for asynchronous Telegram polling and callbacks.
- `aiosqlite` for local asynchronous SQLite access.
- `pydantic-settings` for validated local configuration.
- A small direct JSON-RPC client for Codex app-server, so Pingent can handle streamed events and approvals.
- `pytest`, `pytest-asyncio`, and `ruff` for quality checks.
- `uv` for dependency and virtual-environment management.

Docker, an ORM, and external infrastructure are intentionally excluded until a demonstrated requirement appears. FastAPI and Uvicorn serve the optional local Web SPA.
