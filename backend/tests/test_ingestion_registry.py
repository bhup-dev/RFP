"""Registration decisions. The default tests use an in-memory session stand-in."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.bid import Bid
from app.db.models.document import Document
from app.ingestion.exceptions import IngestionRegistrationError
from app.ingestion.registry import (
    CHANGED,
    DUPLICATE,
    REGISTERED,
    SqlDocumentRegistry,
    StoredDocument,
    classify_registration,
    grouping_identifier,
)
from tests.ingestion_doubles import RecordingSession, _Savepoint


def _hash(seed: str) -> str:
    return f"{seed:0<64}"[:64]


def test_classify_registration_uses_content_and_path() -> None:
    original = StoredDocument(
        document_id=uuid4(),
        bid_id=uuid4(),
        bid_identifier="Open_Bid",
        storage_path="Open_Bid/notes.pdf",
        file_hash=_hash("a"),
    )
    assert classify_registration(file_hash=_hash("a"), by_path=None, by_hash=None) == REGISTERED
    assert classify_registration(file_hash=_hash("a"), by_path=original, by_hash=original) == DUPLICATE
    assert classify_registration(file_hash=_hash("b"), by_path=original, by_hash=None) == CHANGED
    assert classify_registration(file_hash=_hash("a"), by_path=None, by_hash=original) == DUPLICATE


def test_grouping_uses_the_folder_name_only_as_an_internal_id() -> None:
    assert grouping_identifier("Open_Bid/notes.pdf", override=None, input_root_name="bids") == "Open_Bid"
    assert (
        grouping_identifier(
            "City of Springfield/attachments/addendum.html",
            override=None,
            input_root_name="bids",
        )
        == "City of Springfield"
    )
    assert grouping_identifier("loose.pdf", override=None, input_root_name="bids") == "bids"
    assert grouping_identifier("Open_Bid/notes.pdf", override="internal-group", input_root_name="bids") == (
        "internal-group"
    )
    with pytest.raises(IngestionRegistrationError):
        grouping_identifier("Open_Bid/notes.pdf", override="..", input_root_name="bids")
    with pytest.raises(IngestionRegistrationError):
        grouping_identifier("x" * 129 + "/notes.pdf", override=None, input_root_name="bids")


def test_first_registration_does_not_invent_solicitation_metadata() -> None:
    session = RecordingSession()
    registry = SqlDocumentRegistry(session)

    decision = asyncio.run(
        registry.register(
            bid_identifier="City of Springfield",
            relative_path="City of Springfield/notes.pdf",
            file_name="notes.pdf",
            document_type="pdf",
            file_hash=_hash("a"),
        )
    )

    assert decision.outcome == REGISTERED
    assert len(session.bids) == 1
    assert len(session.documents) == 1
    bid = session.bids[0]
    document = session.documents[0]
    assert bid.bid_identifier == "City of Springfield"
    assert bid.solicitation_number is None
    assert bid.title is None
    assert bid.issuing_organization is None
    assert document.storage_path == "City of Springfield/notes.pdf"
    assert document.file_hash == _hash("a")
    assert document.document_type == "pdf"
    assert document.ingestion_status == "pending"
    assert document.document_date is None
    assert document.addendum_number is None
    assert decision.document_id == document.id


def test_repeat_same_path_and_duplicate_content_do_not_create_rows() -> None:
    session = RecordingSession()
    registry = SqlDocumentRegistry(session)
    first = asyncio.run(
        registry.register(
            bid_identifier="Open_Bid",
            relative_path="Open_Bid/notes.pdf",
            file_name="notes.pdf",
            document_type="pdf",
            file_hash=_hash("a"),
        )
    )
    repeated = asyncio.run(
        registry.register(
            bid_identifier="Open_Bid",
            relative_path="Open_Bid/notes.pdf",
            file_name="notes.pdf",
            document_type="pdf",
            file_hash=_hash("a"),
        )
    )
    renamed = asyncio.run(
        registry.register(
            bid_identifier="Open_Bid",
            relative_path="Open_Bid/copy.pdf",
            file_name="copy.pdf",
            document_type="pdf",
            file_hash=_hash("a"),
        )
    )

    assert repeated.outcome == DUPLICATE
    assert renamed.outcome == DUPLICATE
    assert repeated.document_id == first.document_id
    assert renamed.document_id == first.document_id
    assert renamed.bid_identifier == "Open_Bid"
    assert len(session.documents) == 1
    assert session.documents[0].storage_path == "Open_Bid/notes.pdf"
    assert len(session.bids) == 1


def test_identical_content_in_another_bid_is_registered_for_that_bid() -> None:
    session = RecordingSession()
    registry = SqlDocumentRegistry(session)
    first = asyncio.run(
        registry.register(
            bid_identifier="Open_Bid",
            relative_path="Open_Bid/notes.pdf",
            file_name="notes.pdf",
            document_type="pdf",
            file_hash=_hash("a"),
        )
    )
    other = asyncio.run(
        registry.register(
            bid_identifier="Other_Bid",
            relative_path="Other_Bid/copy.pdf",
            file_name="copy.pdf",
            document_type="pdf",
            file_hash=_hash("a"),
        )
    )

    assert other.outcome == REGISTERED
    assert other.document_id != first.document_id
    assert other.bid_identifier == "Other_Bid"
    assert other.bid_id != first.bid_id
    assert len(session.documents) == 2
    assert len(session.bids) == 2


def test_changed_content_keeps_the_stored_hash() -> None:
    session = RecordingSession()
    registry = SqlDocumentRegistry(session)
    first = asyncio.run(
        registry.register(
            bid_identifier="Open_Bid",
            relative_path="Open_Bid/notes.pdf",
            file_name="notes.pdf",
            document_type="pdf",
            file_hash=_hash("a"),
        )
    )
    changed = asyncio.run(
        registry.register(
            bid_identifier="Open_Bid",
            relative_path="Open_Bid/notes.pdf",
            file_name="notes.pdf",
            document_type="pdf",
            file_hash=_hash("b"),
        )
    )

    assert changed.outcome == CHANGED
    assert changed.document_id == first.document_id
    assert len(session.documents) == 1
    assert session.documents[0].file_hash == _hash("a")


def test_database_error_rolls_the_file_back_to_the_savepoint() -> None:
    session = RecordingSession()
    session.fail_after_documents = 0
    registry = SqlDocumentRegistry(session)

    with pytest.raises(IngestionRegistrationError, match="conflicted"):
        asyncio.run(
            registry.register(
                bid_identifier="Open_Bid",
                relative_path="Open_Bid/notes.pdf",
                file_name="notes.pdf",
                document_type="pdf",
                file_hash=_hash("a"),
            )
        )

    assert session.bids == []
    assert session.documents == []


def test_bid_unique_conflict_reuses_the_existing_bid() -> None:
    existing = Bid(id=uuid4(), bid_identifier="Open_Bid")
    session = _ConflictSession(existing)
    registry = SqlDocumentRegistry(session)

    decision = asyncio.run(
        registry.register(
            bid_identifier="Open_Bid",
            relative_path="Open_Bid/notes.pdf",
            file_name="notes.pdf",
            document_type="pdf",
            file_hash=_hash("a"),
        )
    )

    assert decision.outcome == REGISTERED
    assert decision.bid_id == existing.id
    assert decision.bid_identifier == "Open_Bid"
    assert existing.solicitation_number is None
    assert session.conflict_handled is True


class _ConflictSession(RecordingSession):
    """The first bid insert hits the unique constraint, then the bid is visible."""

    def __init__(self, existing: Bid) -> None:
        super().__init__()
        self.existing = existing
        self.conflict_handled = False
        self._bid_lookups = 0

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self)

    async def flush(self) -> None:
        if any(isinstance(item, Bid) and item is not self.existing for item in self.bids):
            self.conflict_handled = True
            raise IntegrityError("INSERT", {}, Exception("unique"))

    async def scalar(self, statement: object) -> Bid | Document | None:
        entity = statement.column_descriptions[0]["entity"]  # type: ignore[attr-defined]
        if entity is Bid:
            self._bid_lookups += 1
            if self._bid_lookups == 1:
                return None
            self.bids = [self.existing]
            return self.existing
        return await super().scalar(statement)
