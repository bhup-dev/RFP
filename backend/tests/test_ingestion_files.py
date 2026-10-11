"""Hashing and path checks. These tests do not touch PostgreSQL."""

import hashlib
import os
from pathlib import Path

import pytest

from app.ingestion.exceptions import IngestionIOError, IngestionPathError
from app.ingestion.file_utils import (
    contains_path,
    file_sha256,
    file_size,
    public_input_label,
    resolve_within_root,
)


def test_sha256_matches_a_streaming_read(tmp_path: Path) -> None:
    payload = b"procurement-bytes" * 50
    path = tmp_path / "notes.pdf"
    path.write_bytes(payload)

    assert file_sha256(path, chunk_size=7) == hashlib.sha256(payload).hexdigest()
    assert file_size(path) == len(payload)


def test_missing_file_reports_a_readable_error(tmp_path: Path) -> None:
    with pytest.raises(IngestionIOError, match="readable"):
        file_sha256(tmp_path / "missing.pdf")


def test_permission_error_reports_a_readable_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "notes.pdf"
    path.write_bytes(b"abc")

    def deny(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "open", deny)
    with pytest.raises(IngestionIOError, match="readable"):
        file_sha256(path)


def test_unreadable_file_is_rejected_when_the_platform_enforces_it(tmp_path: Path) -> None:
    path = tmp_path / "notes.pdf"
    path.write_bytes(b"abc")
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("this platform still allows the owner to read a mode 000 file")
        with pytest.raises(IngestionIOError, match="readable"):
            file_sha256(path)
    finally:
        path.chmod(0o644)


def test_resolved_paths_must_stay_inside_the_input_root(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    (root / "Open_Bid").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    assert contains_path(root, root / "Open_Bid" / "notes.pdf")
    assert not contains_path(root, outside / "notes.pdf")
    assert not contains_path(root, root / "Open_Bid" / ".." / ".." / "outside" / "notes.pdf")

    assert resolve_within_root(root, "Open_Bid") == (root / "Open_Bid").resolve()
    assert resolve_within_root(root, None) == root.resolve()
    with pytest.raises(IngestionPathError, match="inside the configured input root"):
        resolve_within_root(root, "../outside")
    with pytest.raises(IngestionPathError, match="inside the configured input root"):
        resolve_within_root(root, "/etc")
    with pytest.raises(IngestionPathError, match="not a directory"):
        resolve_within_root(tmp_path / "missing", None)

    outside_link = root / "escape"
    outside_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(IngestionPathError, match="inside the configured input root"):
        resolve_within_root(root, "escape")


def test_public_input_label_hides_directories_outside_the_backend(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    assert public_input_label(backend / "data" / "bids") == "data/bids"
    label = public_input_label(tmp_path / "bids")
    assert label == "bids"
    assert "/" not in label
    assert "Users" not in label
