"""Path checks and streaming SHA-256 hashes for source files."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.ingestion.exceptions import IngestionIOError, IngestionPathError

HASH_CHUNK_SIZE = 65536
SUPPORTED_EXTENSIONS = {".pdf", ".html", ".htm"}
DOCUMENT_TYPES = {".pdf": "pdf", ".html": "html", ".htm": "html"}


def document_type_for(path: Path) -> str | None:
    """Return ``pdf`` or ``html`` for a supported extension, ignoring case."""
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        return None
    return DOCUMENT_TYPES[extension]


def public_input_label(path: Path) -> str:
    """Return a backend-relative path, or only the folder name when it lives elsewhere."""
    resolved = path.expanduser().resolve()
    backend_root = Path(__file__).resolve().parents[2]
    try:
        relative = resolved.relative_to(backend_root).as_posix()
    except ValueError:
        return resolved.name
    return relative or "."


def contains_path(root: Path, candidate: Path) -> bool:
    """True when the resolved candidate stays inside the resolved root."""
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    return candidate_resolved == root_resolved or root_resolved in candidate_resolved.parents


def resolve_within_root(root: Path, relative_directory: str | None) -> Path:
    """Resolve a scan directory and reject paths that leave ``root``."""
    root_resolved = root.expanduser().resolve()
    if not root_resolved.is_dir():
        raise IngestionPathError("configured input root is not a directory")
    if relative_directory is None or relative_directory.strip() in {"", "."}:
        return root_resolved

    raw = relative_directory.strip().replace("\\", "/")
    if "\x00" in raw:
        raise IngestionPathError("directory must stay inside the configured input root")
    candidate = Path(raw)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise IngestionPathError("directory must stay inside the configured input root")

    resolved = (root_resolved / candidate).resolve()
    if not contains_path(root_resolved, resolved):
        raise IngestionPathError("directory must stay inside the configured input root")
    if resolved.is_symlink() or not resolved.is_dir():
        raise IngestionPathError("directory is not available")
    return resolved


def resolve_registered_file(root: Path, relative_path: str) -> Path:
    """Resolve a registered storage path and reject paths outside ``root``."""
    root_resolved = root.expanduser().resolve()
    if not root_resolved.is_dir():
        raise IngestionPathError("configured input root is not a directory")
    raw = relative_path.strip().replace("\\", "/")
    if not raw or "\x00" in raw:
        raise IngestionPathError("registered file is outside the configured input root")
    candidate = Path(raw)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise IngestionPathError("registered file is outside the configured input root")
    resolved = (root_resolved / candidate).resolve()
    if resolved == root_resolved or not contains_path(root_resolved, resolved):
        raise IngestionPathError("registered file is outside the configured input root")
    if not resolved.is_file():
        raise IngestionIOError("registered file is not available")
    return resolved


def file_sha256(path: Path, *, chunk_size: int = HASH_CHUNK_SIZE) -> str:
    """Hash a file in chunks. The file contents are not retained."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(chunk_size)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise IngestionIOError("file could not be read; check that it exists and is readable") from exc
    return digest.hexdigest()


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError as exc:
        raise IngestionIOError("file could not be read; check that it exists and is readable") from exc
