from pathlib import Path

import pytest
from pydantic import ValidationError

from pingent.config import Settings


def _settings(workspace: Path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="token",
        allowed_telegram_user_ids={42},
        workspaces=[workspace],
        **overrides,
    )


def test_codex_request_timeout_defaults_to_30_seconds(tmp_path: Path) -> None:
    assert _settings(tmp_path).codex_request_timeout_seconds == 30


def test_codex_request_timeout_accepts_a_positive_value(tmp_path: Path) -> None:
    assert _settings(tmp_path, codex_request_timeout_seconds=90).codex_request_timeout_seconds == 90


@pytest.mark.parametrize("timeout", [0, -1])
def test_codex_request_timeout_must_be_positive(tmp_path: Path, timeout: int) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        _settings(tmp_path, codex_request_timeout_seconds=timeout)
