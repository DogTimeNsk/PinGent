from pathlib import Path

import aiosqlite
import pytest

from pingent.storage import MIGRATIONS, SessionRepository


@pytest.mark.asyncio
async def test_initialize_applies_each_schema_migration(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "pingent.sqlite3")

    await repository.initialize()
    await repository.initialize()

    async with aiosqlite.connect(tmp_path / "pingent.sqlite3") as connection:
        cursor = await connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        assert [row[0] for row in await cursor.fetchall()] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_initialize_migrates_an_existing_pre_migration_database(tmp_path: Path) -> None:
    database_path = tmp_path / "pingent.sqlite3"
    async with aiosqlite.connect(database_path) as connection:
        await connection.executescript(MIGRATIONS[0])
        await connection.execute(
            """
            INSERT INTO sessions (
                id, telegram_user_id, workspace, codex_thread_id, created_at, updated_at
            ) VALUES ('session-1', 42, '/workspace', 'thread-1', 'now', 'now')
            """
        )
        await connection.commit()

    repository = SessionRepository(database_path)
    await repository.initialize()

    session = await repository.get(42, Path("/workspace"))
    assert session is not None
    assert session.codex_thread_id == "thread-1"
    assert await repository.audit_events() == ()


@pytest.mark.asyncio
async def test_audit_events_round_trip_structured_metadata(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "pingent.sqlite3")
    await repository.initialize()

    await repository.record_audit_event(
        "workspace_selected", telegram_user_id=42, metadata={"workspace": "/workspace"}
    )

    events = await repository.audit_events()
    assert len(events) == 1
    assert events[0].event_type == "workspace_selected"
    assert events[0].telegram_user_id == 42
    assert events[0].metadata == {"workspace": "/workspace"}


@pytest.mark.asyncio
async def test_create_web_user_stores_only_a_password_hash(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "pingent.sqlite3")
    await repository.initialize()

    user = await repository.create_web_user("alice", "$argon2id$example")

    assert user.username == "alice"
    async with aiosqlite.connect(tmp_path / "pingent.sqlite3") as connection:
        cursor = await connection.execute(
            "SELECT password_hash FROM web_users WHERE id = ?", (user.id,)
        )
        assert (await cursor.fetchone())[0] == "$argon2id$example"
