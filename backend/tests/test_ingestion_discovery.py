"""File discovery. These tests use temporary directories and do not touch PostgreSQL."""

import os
from pathlib import Path

import pytest

from app.ingestion.discovery import discover_files


def _write(root: Path, relative: str, content: bytes = b"content") -> Path:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_discovers_nested_pdf_and_html_case_insensitively(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    _write(root, "City of Springfield/notes.pdf", b"%PDF")
    _write(root, "City of Springfield/attachments/portal.HTML", b"<html>")
    _write(root, "Second_Bid/form.HTM", b"<html>")
    _write(root, "Second_Bid/skip.txt", b"text")
    _write(root, "Second_Bid/picture.png", b"png")

    batch = discover_files(root, root)
    found = [item.relative_path for item in batch.files]

    assert found == [
        "City of Springfield/attachments/portal.HTML",
        "City of Springfield/notes.pdf",
        "Second_Bid/form.HTM",
        "Second_Bid/picture.png",
        "Second_Bid/skip.txt",
    ]
    types = {item.relative_path: item.document_type for item in batch.files}
    assert types["City of Springfield/notes.pdf"] == "pdf"
    assert types["City of Springfield/attachments/portal.HTML"] == "html"
    assert types["Second_Bid/form.HTM"] == "html"
    assert types["Second_Bid/skip.txt"] is None
    assert types["Second_Bid/picture.png"] is None
    assert batch.errors == ()


def test_empty_directory_discovers_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    (root / "Empty Folder").mkdir(parents=True)

    batch = discover_files(root, root)

    assert batch.files == ()
    assert batch.errors == ()


def test_ignores_hidden_files_and_excluded_directories(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    _write(root, "Bid1/visible.pdf", b"pdf")
    _write(root, "Bid1/.secret.pdf", b"hidden")
    _write(root, "Bid1/.DS_Store", b"mac")
    _write(root, "Bid1/Thumbs.db", b"windows")
    _write(root, "Bid1/desktop.ini", b"windows")
    _write(root, "Bid1/.git/objects.pdf", b"git")
    _write(root, "Bid1/__pycache__/cached.pdf", b"cache")
    _write(root, "Bid1/node_modules/lib.pdf", b"modules")
    _write(root, "Bid1/__MACOSX/resource.pdf", b"mac")

    batch = discover_files(root, root)

    assert [item.relative_path for item in batch.files] == ["Bid1/visible.pdf"]


def test_missing_directory_is_reported_without_its_absolute_path(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    root.mkdir()

    batch = discover_files(root, root / "missing")

    assert batch.files == ()
    assert len(batch.errors) == 1
    assert batch.errors[0].problem == "directory could not be read"
    assert str(tmp_path) not in batch.errors[0].relative_path
    assert str(tmp_path) not in (batch.errors[0].problem or "")


def test_does_not_follow_links_outside_the_input_root(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    outside = tmp_path / "outside"
    _write(outside, "secret.pdf", b"secret")
    _write(outside, "nested/other.pdf", b"other")
    (root / "Open_Bid").mkdir(parents=True)
    (root / "Open_Bid" / "linked.pdf").symlink_to(outside / "secret.pdf")
    (root / "Open_Bid" / "escape").symlink_to(outside, target_is_directory=True)
    _write(root, "Open_Bid/kept.pdf", b"kept")

    batch = discover_files(root, root)
    paths = [item.relative_path for item in batch.files]

    assert "Open_Bid/kept.pdf" in paths
    assert all("secret.pdf" not in path or path == "Open_Bid/linked.pdf" for path in paths)
    assert all("nested/other.pdf" not in path for path in paths)
    linked = next(item for item in batch.files if item.relative_path == "Open_Bid/linked.pdf")
    assert linked.problem == "path is outside the input root"
    assert str(outside) not in linked.relative_path


def test_unreadable_directory_is_reported_when_the_platform_enforces_it(tmp_path: Path) -> None:
    root = tmp_path / "bids"
    blocked = root / "blocked"
    _write(blocked, "hidden.pdf", b"pdf")
    blocked.chmod(0)
    try:
        if os.access(blocked, os.R_OK | os.X_OK):
            pytest.skip("this platform still allows the owner to list a mode 000 directory")
        batch = discover_files(root, root)
    finally:
        blocked.chmod(0o755)

    assert all(item.relative_path != "blocked/hidden.pdf" for item in batch.files)
    assert any(item.problem == "directory could not be read" for item in batch.errors)
    assert all(str(tmp_path) not in item.relative_path for item in batch.errors)
