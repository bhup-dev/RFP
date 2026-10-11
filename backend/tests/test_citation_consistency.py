"""Citation resolution and PostgreSQL-to-Qdrant identity auditing."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.ingestion.consistency import ConsistencyAuditor
from app.retrieval.citations import CitationResolver
from app.schemas.retrieval import RetrievedChunk
from app.services.qdrant_service import IndexedPoint


class Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class Session:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def execute(self, statement):
        self.calls += 1
        return Rows(self.rows)


def _rows():
    bid_id, document_id, chunk_id = uuid4(), uuid4(), uuid4()
    document = SimpleNamespace(
        id=document_id,
        bid_id=bid_id,
        file_name="addendum.pdf",
    )
    chunk = SimpleNamespace(
        id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        page_number=2,
        section_title="Warranty",
        chunk_type="text",
        text="Warranty is three years.",
        content_hash="hash-1",
        chunk_metadata={
            "section_title": "Warranty",
            "table_id": None,
            "anchor": None,
        },
    )
    return bid_id, document_id, chunk_id, chunk, document


def _result(bid_id, document_id, chunk_id, **updates):
    values = {
        "bid_id": bid_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "file_name": "addendum.pdf",
        "text": "Warranty is three years.",
        "page_number": 2,
        "section_title": "Warranty",
        "chunk_type": "text",
        "metadata": {"anchor": None, "table_id": None},
    }
    values.update(updates)
    return RetrievedChunk(**values)


def test_citations_resolve_in_one_query_and_reject_fabricated_sources() -> None:
    bid_id, document_id, chunk_id, chunk, document = _rows()
    session = Session([(chunk, document)])
    valid = _result(bid_id, document_id, chunk_id)
    missing = _result(bid_id, document_id, uuid4())
    wrong_page = _result(bid_id, document_id, chunk_id, page_number=9)
    result = asyncio.run(
        CitationResolver(session).validate([valid, missing, wrong_page])
    )
    assert session.calls == 1
    assert result.checked == 3
    assert result.resolved == 1
    assert [failure.reason for failure in result.failures] == [
        "chunk does not exist",
        "page number does not match chunk",
    ]
    assert all("storage" not in failure.reason for failure in result.failures)


class Points:
    def __init__(self, records):
        self.records = records
        self.requested = []

    def retrieve(self, point_ids):
        self.requested.extend(str(value) for value in point_ids)
        return [
            self.records[str(value)]
            for value in point_ids
            if str(value) in self.records
        ]


def test_consistency_audit_checks_stable_point_ids_and_payloads() -> None:
    bid_id, document_id, chunk_id, chunk, document = _rows()
    payload = {
        "bid_id": str(bid_id),
        "document_id": str(document_id),
        "chunk_id": str(chunk_id),
        "file_name": document.file_name,
        "page_number": chunk.page_number,
        "chunk_type": chunk.chunk_type,
        "content_hash": chunk.content_hash,
        "text": chunk.text,
    }
    points = Points(
        {
            str(chunk_id): IndexedPoint(
                point_id=str(chunk_id),
                payload=payload,
            )
        }
    )
    report = asyncio.run(
        ConsistencyAuditor(Session([(chunk, document)]), points).audit(
            bid_id=bid_id
        )
    )
    assert report.chunks_checked == 1
    assert report.points_found == 1
    assert report.failures == []
    assert points.requested == [str(chunk_id)]

    points.records[str(chunk_id)] = IndexedPoint(
        point_id=str(chunk_id),
        payload={**payload, "document_id": str(uuid4())},
    )
    mismatch = asyncio.run(
        ConsistencyAuditor(Session([(chunk, document)]), points).audit()
    )
    assert mismatch.failures[0].code == "payload_mismatch"
    assert "document_id" in mismatch.failures[0].message


def test_consistency_audit_reports_missing_points_without_repairing() -> None:
    _bid_id, _document_id, chunk_id, chunk, document = _rows()
    points = Points({})
    report = asyncio.run(
        ConsistencyAuditor(Session([(chunk, document)]), points).audit()
    )
    assert report.chunks_checked == 1
    assert report.points_found == 0
    assert report.failures[0].chunk_id == chunk_id
    assert report.failures[0].code == "missing_point"
