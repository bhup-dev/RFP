"""Local directory reserved for files created by later ingestion tasks."""

from pathlib import Path


def ensure_storage_directory(path: Path) -> Path:
    """Create the configured storage directory when the process starts."""
    path.mkdir(parents=True, exist_ok=True)
    return path
