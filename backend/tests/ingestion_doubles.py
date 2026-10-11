"""In-memory session stand-in for registration tests. It does not open PostgreSQL."""

from __future__ import annotations

from app.db.models.bid import Bid
from app.db.models.document import Document


class _Savepoint:
    def __init__(self, session: RecordingSession) -> None:
        self._session = session
        self._bids = list(session.bids)
        self._documents = list(session.documents)

    async def __aenter__(self) -> _Savepoint:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if exc_type is not None:
            self._session.bids = self._bids
            self._session.documents = self._documents
        return False


class RecordingSession:
    """Enough of an async session to exercise SqlDocumentRegistry."""

    def __init__(self) -> None:
        self.bids: list[Bid] = []
        self.documents: list[Document] = []
        self.fail_after_documents: int | None = None

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self)

    def add(self, obj: Bid | Document) -> None:
        if isinstance(obj, Bid):
            self.bids.append(obj)
        elif isinstance(obj, Document):
            self.documents.append(obj)

    async def flush(self) -> None:
        if (
            self.fail_after_documents is not None
            and len(self.documents) > self.fail_after_documents
        ):
            raise self._database_error()

    async def scalar(self, statement: object) -> Bid | Document | None:
        entity = statement.column_descriptions[0]["entity"]  # type: ignore[attr-defined]
        rows: list[Bid] | list[Document]
        if entity is Document:
            rows = self.documents
        elif entity is Bid:
            rows = self.bids
        else:
            return None
        for row in rows:
            if _matches(row, statement, self.bids):
                return row
        return None

    async def get(self, model: type[Bid] | type[Document], ident: object) -> Bid | Document | None:
        if model is Bid:
            for bid in self.bids:
                if bid.id == ident:
                    return bid
        if model is Document:
            for document in self.documents:
                if document.id == ident:
                    return document
        return None

    @staticmethod
    def _database_error() -> Exception:
        from sqlalchemy.exc import OperationalError

        return OperationalError("INSERT", {}, Exception("connection closed"))


def _matches(row: Bid | Document, statement: object, bids: list[Bid]) -> bool:
    clause = statement.whereclause  # type: ignore[attr-defined]
    clauses = getattr(clause, "clauses", None) or [clause]
    return all(_matches_one(row, item, bids) for item in clauses)


def _matches_one(row: Bid | Document, clause: object, bids: list[Bid]) -> bool:
    key = clause.left.key  # type: ignore[attr-defined]
    expected = clause.right.value  # type: ignore[attr-defined]
    if isinstance(row, Document) and clause.left.table.name == "bids":  # type: ignore[attr-defined]
        bid = next((item for item in bids if item.id == row.bid_id), None)
        return bid is not None and getattr(bid, key) == expected
    return getattr(row, key) == expected
