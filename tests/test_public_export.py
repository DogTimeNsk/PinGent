import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tools" / "export_public_repo.py"
MANIFEST = ROOT / "tools" / "public-files.txt"


def _export_module():
    spec = importlib.util.spec_from_file_location("public_export", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_manifest_is_an_allow_list_without_internal_files() -> None:
    paths = _export_module().public_paths(MANIFEST)

    assert "src" in paths
    assert "README.md" in paths
    assert "TODO.md" not in paths
    assert "AGENTS.md" not in paths
    assert "start_prompt.md" not in paths


def test_export_contains_only_the_allow_listed_revision(tmp_path: Path) -> None:
    destination = tmp_path / "public"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--destination", str(destination)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (destination / "src" / "pingent" / "service.py").is_file()
    assert (destination / "README.md").is_file()
    assert not (destination / "TODO.md").exists()
    assert not (destination / "AGENTS.md").exists()
    assert not (destination / "start_prompt.md").exists()
