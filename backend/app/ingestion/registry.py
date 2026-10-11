"""Register bid and document metadata. PostgreSQL remains the source of truth."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.bid import Bid
from app.db.models.document import Document
from app.ingestion.exceptions import IngestionRegistrationError

REGISTERED = "registered"
DUPLICATE = "duplicate"
CHANGED = "changed"
PENDING_STATUS = "pending"


@dataclass(frozen=True)
class StoredDocument:
    """The fields needed to decide whether a file is new, repeated, or changed."""

    document_id: UUID
    bid_id: UUID
    bid_identifier: str
    storage_path: str
    file_hash: str | None


@dataclass(frozen=True)
class RegistrationDecision:
    """Outcome of comparing one file with documents already stored."""

    outcome: str
    document_id: UUID
    bid_id: UUID
    bid_identifier: str


def classify_registration(
    *,
    file_hash: str,
    by_path: StoredDocument | None,
    by_hash: StoredDocument | None,
) -> str:
    """Decide how a file relates to existing rows. This does not write anything."""
    if by_path is not None:
        if by_path.file_hash == file_hash:
            return DUPLICATE
        return CHANGED
    if by_hash is not None:
        return DUPLICATE
    return REGISTERED


def grouping_identifier(
    relative_path: str,
    *,
    override: str | None,
    input_root_name: str,
) -> str:
    """Use an explicit id, otherwise the first folder under the input root.

    The result is the internal ``bid_identifier``. It is not a solicitation number.
    """
    if override is not None and override.strip():
        return _validate_identifier(override)
    parts = tuple(part for part in relative_path.split("/") if part not in {"", "."})
    if len(parts) >= 2:
        return _validate_identifier(parts[0])
    return _validate_identifier(input_root_name)


def _validate_identifier(value: str) -> str:
    identifier = value.strip()
    if not identifier or identifier in {".", ".."} or "/" in identifier or "\\" in identifier:
        raise IngestionRegistrationError("bid grouping identifier is not usable")
    if len(identifier) > 128:
        raise IngestionRegistrationError("bid grouping identifier is longer than 128 characters")
    return identifier


class SqlDocumentRegistry:
    """Persist registration decisions with the existing async session.

    A repeated path with the same hash is left unchanged. A repeated path with
    a different hash is reported as changed and the stored row is not updated.
    The same hash at a new path in the same bid keeps the original row and its
    source path. Identical content in a different bid is registered separately,
    because each bid must own its own evidence.

    ``documents`` has no unique constraint on ``file_hash`` or ``storage_path``.
    Each file uses a savepoint so one failure does not roll back the others.
    Two overlapping scans can still insert the same new hash once each.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register(
        self,
        *,
        bid_identifier: str,
        relative_path: str,
        file_name: str,
        document_type: str,
        file_hash: str,
    ) -> RegistrationDecision:
        if len(file_name) > 512 or len(relative_path) > 1024 or len(file_hash) != 64:
            raise IngestionRegistrationError("document metadata does not fit the registered columns")
        try:
            async with self._session.begin_nested():
                return await self._register_in_savepoint(
                    bid_identifier=bid_identifier,
                    relative_path=relative_path,
                    file_name=file_name,
                    document_type=document_type,
                    file_hash=file_hash,
                )
        except IngestionRegistrationError:
            raise
        except SQLAlchemyError as exc:
            raise IngestionRegistrationError("document registration conflicted") from exc

    async def _register_in_savepoint(
        self,
        *,
        bid_identifier: str,
        relative_path: str,
        file_name: str,
        document_type: str,
        file_hash: str,
    ) -> RegistrationDecision:
        by_path = await self._by_path(relative_path)
        by_hash = await self._by_hash(file_hash, bid_identifier)
        outcome = classify_registration(file_hash=file_hash, by_path=by_path, by_hash=by_hash)
        existing = by_path if by_path is not None else by_hash
        if outcome != REGISTERED and existing is not None:
            return RegistrationDecision(
                outcome=outcome,
                document_id=existing.document_id,
                bid_id=existing.bid_id,
                bid_identifier=existing.bid_identifier,
            )

        bid = await self._get_or_create_bid(bid_identifier)
        document = Document(
            id=uuid4(),
            bid_id=bid.id,
            file_name=file_name,
            document_type=document_type,
            file_hash=file_hash,
            storage_path=relative_path,
            ingestion_status=PENDING_STATUS,
        )
        self._session.add(document)
        await self._session.flush()
        return RegistrationDecision(
            outcome=REGISTERED,
            document_id=document.id,
            bid_id=bid.id,
            bid_identifier=bid.bid_identifier,
        )

    async def _by_path(self, relative_path: str) -> StoredDocument | None:
        document = await self._session.scalar(
            select(Document).where(Document.storage_path == relative_path)
        )
        return await self._stored(document)

    async def _by_hash(self, file_hash: str, bid_identifier: str) -> StoredDocument | None:
        document = await self._session.scalar(
            select(Document)
            .join(Bid, Bid.id == Document.bid_id)
            .where(Document.file_hash == file_hash, Bid.bid_identifier == bid_identifier)
        )
        return await self._stored(document)

    async def _stored(self, document: Document | None) -> StoredDocument | None:
        if document is None:
            return None
        bid = await self._session.get(Bid, document.bid_id)
        identifier = bid.bid_identifier if bid is not None else ""
        return StoredDocument(
            document_id=document.id,
            bid_id=document.bid_id,
            bid_identifier=identifier,
            storage_path=document.storage_path,
            file_hash=document.file_hash,
        )

    async def _get_or_create_bid(self, identifier: str) -> Bid:
        existing = await self._session.scalar(select(Bid).where(Bid.bid_identifier == identifier))
        if existing is not None:
            return existing
        bid = Bid(id=uuid4(), bid_identifier=identifier)
        try:
            async with self._session.begin_nested():
                self._session.add(bid)
                await self._session.flush()
        except IntegrityError as exc:
            found = await self._session.scalar(select(Bid).where(Bid.bid_identifier == identifier))
            if found is None:
                raise IngestionRegistrationError("bid registration conflicted") from exc
            return found
        return bid
