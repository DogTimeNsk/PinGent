# Codex CLI compatibility

Pingent's current integration-test baseline is **Codex CLI 0.161.0**. The
adapter uses the local `codex app-server` JSON-RPC interface over standard input
and output. That interface is experimental and its generated schema is specific
to the CLI version, so a later version is not implicitly supported.

The default test suite also exercises Pingent's JSON-RPC transport against a
minimal local subprocess and its Telegram router against an in-memory aiogram
session. Those tests cover adapter boundaries without network access, but they
do not replace this check against the installed Codex CLI.

Before upgrading Codex, run the opt-in integration check on the same local
computer that will run Pingent:

```bash
PINGENT_RUN_CODEX_INTEGRATION=1 uv run pytest -m integration
```

The check requires `codex` on `PATH`, verifies its exact version, then starts
`codex app-server` and exercises Pingent's `initialize` / `initialized`
handshake. The restart-resume check also completes one harmless text-only turn,
so it requires a local Codex login and uses the selected model, but it does not
modify the workspace. Run it on a normal Linux host: Codex's sandbox needs user
namespaces, which restricted CI containers may not provide.

To test a non-default binary path, set `PINGENT_CODEX_BINARY` to its executable
path. If the version check fails, do not rely on Pingent until its adapter and
integration fixture have been updated and revalidated for that CLI version.

The official Codex documentation recommends generating app-server schemas from
the exact CLI version being used:

```bash
codex app-server generate-json-schema --out ./schemas
```

Use that schema when changing Pingent's JSON-RPC adapter.
