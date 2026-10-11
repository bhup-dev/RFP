"""Recursive discovery of PDF and HTML files under an input root."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.file_utils import contains_path, document_type_for

EXCLUDED_DIRECTORY_NAMES = frozenset({".git", ".venv", "__pycache__", "__MACOSX", "node_modules"})
IGNORED_FILE_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


@dataclass(frozen=True)
class DiscoveredFile:
    """One filesystem entry relative to the configured input root."""

    relative_path: str
    path: Path
    document_type: str | None
    problem: str | None = None

    @property
    def supported(self) -> bool:
        return self.document_type is not None and self.problem is None


@dataclass(frozen=True)
class DiscoveryBatch:
    """Supported files, unsupported files, and directories that could not be read."""

    files: tuple[DiscoveredFile, ...]
    errors: tuple[DiscoveredFile, ...]


def discover_files(input_root: Path, scan_root: Path) -> DiscoveryBatch:
    """Walk ``scan_root`` without following directory links that leave ``input_root``."""
    root = input_root.resolve()
    start = scan_root.resolve()
    files: list[DiscoveredFile] = []
    errors: list[DiscoveredFile] = []

    def onerror(exc: OSError) -> None:
        errors.append(
            DiscoveredFile(
                relative_path=_safe_relative(root, getattr(exc, "filename", None)),
                path=root,
                document_type=None,
                problem="directory could not be read",
            )
        )

    for dirpath, dirnames, filenames in os.walk(start, followlinks=False, onerror=onerror):
        dirnames[:] = sorted(name for name in dirnames if _include_directory(name))
        current = Path(dirpath)
        for name in sorted(filenames):
            if _ignore_file(name):
                continue
            path = current / name
            record = _inspect_file(root, path)
            if record is not None:
                files.append(record)

    files.sort(key=lambda item: item.relative_path)
    errors.sort(key=lambda item: item.relative_path)
    return DiscoveryBatch(files=tuple(files), errors=tuple(errors))


def _inspect_file(root: Path, path: Path) -> DiscoveredFile | None:
    relative = _relative_posix(root, path)
    try:
        is_link = path.is_symlink()
        resolved = path.resolve()
    except OSError:
        return DiscoveredFile(relative, path, document_type_for(path), "file could not be read")

    if is_link and not contains_path(root, resolved):
        return DiscoveredFile(
            relative,
            path,
            document_type_for(path),
            "path is outside the input root",
        )
    if not resolved.is_file():
        return None
    if not contains_path(root, resolved):
        return DiscoveredFile(
            relative,
            path,
            document_type_for(path),
            "path is outside the input root",
        )
    return DiscoveredFile(relative, resolved, document_type_for(path), None)


def _include_directory(name: str) -> bool:
    return not name.startswith(".") and name not in EXCLUDED_DIRECTORY_NAMES


def _ignore_file(name: str) -> bool:
    return name.startswith(".") or name.lower() in IGNORED_FILE_NAMES


def _relative_posix(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _safe_relative(root: Path, filename: str | None) -> str:
    if not filename:
        return "."
    try:
        return Path(filename).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return Path(filename).name
