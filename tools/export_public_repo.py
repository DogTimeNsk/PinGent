#!/usr/bin/env python3
"""Export an allow-listed, committed revision into a new public repository directory."""

from __future__ import annotations

import argparse
import io
import subprocess
import tarfile
from pathlib import Path


def public_paths(manifest: Path) -> tuple[str, ...]:
    """Read non-comment, repository-relative paths from an export manifest."""
    paths = tuple(
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not paths or any(Path(path).is_absolute() or ".." in Path(path).parts for path in paths):
        raise ValueError(f"Invalid public export manifest: {manifest}")
    return paths


def git_output(source: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(source), *arguments],
        check=True,
        capture_output=True,
    ).stdout


def paths_at_revision(source: Path, revision: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        path
        for path in paths
        if subprocess.run(
            ["git", "-C", str(source), "cat-file", "-e", f"{revision}:{path}"],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def export_revision(source: Path, destination: Path, revision: str, paths: tuple[str, ...]) -> None:
    """Extract the allowed paths from a committed Git revision into an empty directory."""
    source = Path(git_output(source, "rev-parse", "--show-toplevel").decode().strip())
    destination = destination.resolve()
    if destination == source or source in destination.parents:
        raise ValueError("The export destination must be outside the source repository")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("The export destination must be a new or empty directory")

    selected_paths = paths_at_revision(source, revision, paths)
    if not selected_paths:
        raise ValueError(f"No public paths exist at revision {revision}")
    archive = git_output(source, "archive", "--format=tar", revision, *selected_paths)

    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as contents:
        for member in contents.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ValueError(f"Unsafe path in Git archive: {member.name}")
        contents.extractall(destination)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="new or empty directory that will receive the public export",
    )
    parser.add_argument(
        "--source",
        default=Path.cwd(),
        type=Path,
        help="private Git working tree (default: current directory)",
    )
    parser.add_argument(
        "--ref",
        default="HEAD",
        help="committed Git revision to export (default: HEAD)",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    manifest = Path(__file__).with_name("public-files.txt")
    try:
        export_revision(
            arguments.source,
            arguments.destination,
            arguments.ref,
            public_paths(manifest),
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        raise SystemExit(f"Public export failed: {error}") from error
    print(f"Exported public revision {arguments.ref} to {arguments.destination.resolve()}")


if __name__ == "__main__":
    main()
