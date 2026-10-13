# External channel adapters

Pingent can supervise a trusted local channel adapter implemented in another
language. This is an extension point for one self-hosted installation, not a
network API and not a plugin marketplace. The Core remains responsible for
authorization, workspace restrictions, queues, sessions, auditing, and Codex.

## Protocol version 1

The Core starts the configured command directly, without a shell, and uses
newline-delimited JSON through its standard input and output. The child process
receives no Pingent secrets in its environment. It must emit exactly this first
line:

```json
{"type":"hello","protocol_version":1}
```

The Core replies with `initialize`, including a fixed `actor_id`. The adapter
must never choose or send an actor ID. To submit a task it writes:

```json
{"type":"incoming_message","text":"Run the test suite"}
```

The Core responds with `queued` and later sends `task` notifications. Version 1
only supports plain task submission and delivery. It deliberately has no shell
escape, arbitrary workspace path, approval action, or database access. A Codex
permission request from an external-adapter task is recorded and declined.

## PHP console example

`examples/php-console-adapter/adapter.php` is an executable PHP 8.2+ example.
It reads Core protocol messages from standard input, writes protocol messages to
standard output, and reads the interactive user input from `/dev/tty`.

Configure it with an absolute path in `.env`:

```dotenv
PINGENT_EXTERNAL_ADAPTERS=[{"command":["/usr/bin/php","/absolute/path/to/pingent/examples/php-console-adapter/adapter.php"],"actor_id":9001}]
```

`actor_id` is a positive, installation-local identifier. It must be unique and
must not equal an allowed Telegram user ID. Start Pingent from an interactive
terminal; type a task into that terminal after the adapter announces itself.
PHP is not a Pingent runtime dependency: installations that do not configure
this adapter do not need PHP.

When adding another adapter, keep its protocol output restricted to JSON Lines,
validate all platform input before emitting it, and treat Core output as display
data rather than commands.
