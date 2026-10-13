"""Fixtures for opt-in tests against the local Codex CLI."""

import os
import re
import shutil
import subprocess

import pytest

SUPPORTED_CODEX_VERSION = "0.161.0"


@pytest.fixture
def codex_binary() -> str:
    """Return the verified local Codex binary, or skip opt-in integration tests."""
    if os.environ.get("PINGENT_RUN_CODEX_INTEGRATION") != "1":
        pytest.skip("Set PINGENT_RUN_CODEX_INTEGRATION=1 to run Codex integration tests")

    binary = os.environ.get("PINGENT_CODEX_BINARY", "codex")
    if shutil.which(binary) is None:
        pytest.fail(f"Codex binary was not found: {binary}")

    result = subprocess.run(
        [binary, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        pytest.fail(f"Could not read the Codex CLI version: {result.stderr.strip()}")

    match = re.search(r"\b(\d+\.\d+\.\d+)\b", result.stdout)
    if match is None:
        pytest.fail(f"Could not parse the Codex CLI version: {result.stdout.strip()}")
    if match.group(1) != SUPPORTED_CODEX_VERSION:
        pytest.fail(f"Pingent supports Codex CLI {SUPPORTED_CODEX_VERSION}; found {match.group(1)}")
    return binary
