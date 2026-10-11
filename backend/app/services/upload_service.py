"""Validate, store, and register browser-uploaded bid documents.

Files land under ``ingestion_input_root/<bid_identifier>/`` so the existing
registry, parse, chunk, and index stages process them without changes.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.bid import Bid
from app.db.models.document import Document
from app.ingestion.exceptions import IngestionError
from app.ingestion.file_utils import DOCUMENT_TYPES, contains_path
from app.ingestion.registry import DUPLICATE, SqlDocumentRegistry

UPLOAD_CHUNK_SIZE = 1024 * 1024
SNIFF_BYTES = 4096
MAX_STORED_NAME_LENGTH = 180
DOCUMENT_ROLES = ("original", "addendum", "clarification", "attachment")

OUTCOME_REGISTERED = "registered"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_REJECTED = "rejected"

_UNSAFE_NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._ ()\-]+")
_REPEATED_SEPARATORS = re.compile(r"[ _]{2,}")
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body", b"<table", b"<div", b"<p>")


class UploadRejected(Exception):
    """A file failed validation. The message is safe to return to the uploader."""


class UploadSource(Protocol):
    """The subset of ``fastapi.UploadFile`` used here."""

    filename: str | None

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class UploadRequestItem:
    source: UploadSource
    role: str | None = None
    addendum_number: int | None = None


@dataclass(frozen=True)
class UploadOutcome:
    original_filename: str
    outcome: str
    document_id: UUID | None = None
    stored_name: str | None = None
    size_bytes: int | None = None
    file_hash: str | None = None
    role: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class StagedFile:
    temp_path: Path
    sha256: str
    size_bytes: int


def sanitize_filename(raw: str | None) -> str:
    """Return a safe base name, or raise when nothing usable remains."""
    if raw is None:
        raise UploadRejected("file name is missing")
    base = raw.replace("\\", "/").split("/")[-1]
    base = unicodedata.normalize("NFKC", base).replace("\x00", "")
    base = _UNSAFE_NAME_CHARACTERS.sub("_", base)
    base = _REPEATED_SEPARATORS.sub("_", base).strip(" ._")
    if not base or "." not in base:
        raise UploadRejected("file name must include a supported extension")
    stem, _, extension = base.rpartition(".")
    stem = stem.strip(" ._") or "document"
    extension = extension.lower()
    limit = MAX_STORED_NAME_LENGTH - len(extension) - 1
    return f"{stem[:limit]}.{extension}"


def extension_of(name: str) -> str:
    return "." + name.rpartition(".")[2].lower() if "." in name else ""


def content_matches_extension(head: bytes, extension: str) -> bool:
    """Check leading bytes; the extension and client MIME type are not trusted."""
    if extension == ".pdf":
        return b"%PDF-" in head[:1024]
    if extension in {".html", ".htm"}:
        if b"\x00" in head:
            return False
        lowered = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        return any(marker in lowered for marker in _HTML_MARKERS)
    return False


def normalize_role(role: str | None) -> str | None:
    if role is None or not role.strip() or role.strip().lower() == "auto":
        return None
    normalized = role.strip().lower()
    if normalized not in DOCUMENT_ROLES:
        raise UploadRejected(f"role must be one of: auto, {', '.join(DOCUMENT_ROLES)}")
    return normalized


async def stage_upload(source: UploadSource, directory: Path, max_bytes: int) -> StagedFile:
    """Stream to a hidden temp file in ``directory``, hashing and size-checking as it goes."""
    temp_path = directory / f".upload-{uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp_path.open("xb") as handle:
            while True:
                block = await source.read(UPLOAD_CHUNK_SIZE)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise UploadRejected(
                        f"file is larger than the {max_bytes // (1024 * 1024) or 1} MB limit"
                    )
                digest.update(block)
                handle.write(block)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    if size == 0:
        temp_path.unlink(missing_ok=True)
        raise UploadRejected("file is empty")
    return StagedFile(temp_path=temp_path, sha256=digest.hexdigest(), size_bytes=size)


def _read_head(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(SNIFF_BYTES)


@dataclass
class UploadService:
    """Store and register uploads for one bid inside the caller's transaction."""

    session: AsyncSession
    input_root: Path
    max_file_bytes: int
    allowed_extensions: frozenset[str]
    written_files: list[Path] = field(default_factory=list)

    async def upload(self, bid: Bid, items: list[UploadRequestItem]) -> list[UploadOutcome]:
        directory = self._bid_directory(bid.bid_identifier)
        outcomes: list[UploadOutcome] = []
        for item in items:
            outcomes.append(await self._upload_one(bid, directory, item))
        return outcomes

    def discard_written_files(self) -> None:
        """Remove files written by this request when its transaction is not committed."""
        for path in self.written_files:
            path.unlink(missing_ok=True)
        self.written_files.clear()

    def _bid_directory(self, bid_identifier: str) -> Path:
        root = self.input_root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        directory = (root / bid_identifier).resolve()
        if directory == root or not contains_path(root, directory):
            raise UploadRejected("bid identifier cannot be used as a storage folder")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    async def _upload_one(
        self, bid: Bid, directory: Path, item: UploadRequestItem
    ) -> UploadOutcome:
        original = (item.source.filename or "").strip()[:512] or "(unnamed)"
        staged: StagedFile | None = None
        try:
            role = normalize_role(item.role)
            if item.addendum_number is not None and not 1 <= item.addendum_number <= 999:
                raise UploadRejected("addendum number must be from 1 to 999")
            safe_name = sanitize_filename(item.source.filename)
            extension = extension_of(safe_name)
            if extension not in self.allowed_extensions:
                allowed = ", ".join(sorted(self.allowed_extensions))
                raise UploadRejected(f"file type is not supported; allowed: {allowed}")
            staged = await stage_upload(item.source, directory, self.max_file_bytes)
            if not content_matches_extension(_read_head(staged.temp_path), extension):
                raise UploadRejected(f"file content is not a valid {extension[1:].upper()} file")

            existing = await self._existing_in_bid(bid.id, staged.sha256)
            if existing is not None:
                return UploadOutcome(
                    original_filename=original,
                    outcome=OUTCOME_DUPLICATE,
                    document_id=existing.id,
                    stored_name=existing.file_name,
                    size_bytes=staged.size_bytes,
                    file_hash=staged.sha256,
                    role=existing.document_role,
                    reason="identical content is already part of this bid",
                )

            stored_name = f"{uuid4().hex[:8]}_{safe_name}"
            final_path = directory / stored_name
            os.replace(staged.temp_path, final_path)
            digest, size = staged.sha256, staged.size_bytes
            staged = None
            self.written_files.append(final_path)
            return await self._register(
                bid, final_path, stored_name, original, item, role, extension, digest, size
            )
        except UploadRejected as exc:
            return UploadOutcome(
                original_filename=original, outcome=OUTCOME_REJECTED, reason=str(exc)
            )
        except IngestionError as exc:
            return UploadOutcome(
                original_filename=original, outcome=OUTCOME_REJECTED, reason=str(exc)
            )
        finally:
            if staged is not None:
                staged.temp_path.unlink(missing_ok=True)

    async def _register(
        self,
        bid: Bid,
        final_path: Path,
        stored_name: str,
        original: str,
        item: UploadRequestItem,
        role: str | None,
        extension: str,
        digest: str,
        size: int,
    ) -> UploadOutcome:
        decision = await SqlDocumentRegistry(self.session).register(
            bid_identifier=bid.bid_identifier,
            relative_path=f"{bid.bid_identifier}/{stored_name}",
            file_name=stored_name,
            document_type=DOCUMENT_TYPES[extension],
            file_hash=digest,
        )
        if decision.outcome == DUPLICATE or decision.bid_id != bid.id:
            final_path.unlink(missing_ok=True)
            self.written_files.remove(final_path)
            return UploadOutcome(
                original_filename=original,
                outcome=OUTCOME_DUPLICATE,
                document_id=decision.document_id,
                size_bytes=size,
                file_hash=digest,
                reason="identical content is already part of this bid",
            )
        document = await self.session.get(Document, decision.document_id)
        if document is not None:
            document.document_role = role
            document.addendum_number = item.addendum_number
            document.original_filename = original
            document.uploaded_at = datetime.now(UTC)
            await self.session.flush()
        return UploadOutcome(
            original_filename=original,
            outcome=OUTCOME_REGISTERED,
            document_id=decision.document_id,
            stored_name=stored_name,
            size_bytes=size,
            file_hash=digest,
            role=role,
        )

    async def _existing_in_bid(self, bid_id: UUID, file_hash: str) -> Document | None:
        return await self.session.scalar(
            select(Document).where(Document.bid_id == bid_id, Document.file_hash == file_hash)
        )
