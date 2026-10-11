"""Chunk identity, metadata, and replacement. The unit tests use an in-memory store."""

import asyncio
import json
from datetime import date
from typing import Self
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.db.models.chunk import DocumentChunk
from app.ingestion.chunking.base import ChunkDocument, same_chunk_set
from app.ingestion.chunking.pipeline import ChunkingPipeline, SqlChunkRepository
from app.ingestion.parsers.base import RegisteredSource
from app.ingestion.parsers.html_parser import HtmlParser
from app.ingestion.parsers.pdf_parser import PdfParser
from app.schemas.parsing import (
    ParsedDocument,
    ParsedSection,
    ParsedTable,
    ParseMessage,
    ParseStatus,
)
from tests.pdf_samples import write_table_pdf, write_text_pdf


class MemoryChunkStore:
    """In-memory stand-in that follows the same skip-or-replace rule as PostgreSQL."""

    def __init__(self) -> None:
        self.rows: dict = {}

    async def save(self, document_id, drafts):
        from app.ingestion.chunking.pipeline import SaveCounts

        existing = self.rows.get(document_id, [])
        if same_chunk_set(existing, drafts):
            return SaveCounts(created=0, skipped=len(existing))
        self.rows[document_id] = list(drafts)
        return SaveCounts(created=len(drafts), skipped=0)


def _document(document_id=None, bid_id=None) -> ChunkDocument:
    return ChunkDocument(
        document_id=document_id or uuid4(),
        bid_id=bid_id or uuid4(),
        file_name="notes.pdf",
        file_type="pdf",
        relative_path="Open_Bid/notes.pdf",
        document_date=None,
        addendum_number=None,
    )


def _parsed(document_id, sections, *, status=ParseStatus.PARSED, errors=None) -> ParsedDocument:
    return ParsedDocument(
        document_id=document_id,
        relative_path="Open_Bid/notes.pdf",
        file_name="notes.pdf",
        document_type="pdf",
        parser_name="test",
        parser_version="0",
        status=status,
        text="\n".join(section.text for section in sections),
        sections=sections,
        tables=[table for section in sections for table in section.tables],
        warnings=[],
        errors=errors or [],
        metadata={},
    )


def _pipeline(store: MemoryChunkStore | None = None, **sizes: int) -> tuple[ChunkingPipeline, MemoryChunkStore]:
    memory = store or MemoryChunkStore()
    pipeline = ChunkingPipeline(
        memory,
        chunk_size=sizes.get("chunk_size", 1000),
        chunk_overlap=sizes.get("chunk_overlap", 0),
        table_chunk_max_rows=sizes.get("table_chunk_max_rows", 20),
        chunk_min_text_length=sizes.get("chunk_min_text_length", 50),
    )
    return pipeline, memory


def test_page_metadata_and_distinct_documents() -> None:
    first = _document()
    second = _document()
    parsed = _parsed(
        first.document_id,
        [
            ParsedSection(page_number=1, text="Page one specification " * 20),
            ParsedSection(page_number=2, section_title="Scope", text="Second page only"),
        ],
    )
    pipeline, store = _pipeline(chunk_size=80, chunk_overlap=10)
    other, _other_store = _pipeline(chunk_size=80, chunk_overlap=10)

    summary = asyncio.run(pipeline.persist(first, parsed))
    asyncio.run(other.persist(second, parsed.model_copy(update={"document_id": second.document_id})))

    rows = store.rows[first.document_id]
    assert summary.chunks_created == len(rows)
    assert summary.text_chunks_created == len(rows)
    assert summary.table_chunks_created == 0
    assert all(len(row.text) <= 80 for row in rows if row.page_number == 1)
    assert {row.page_number for row in rows} == {1, 2}
    assert all(row.metadata["page_number"] == row.page_number for row in rows)
    assert rows[-1].metadata["section_title"] == "Scope"
    assert rows[-1].text == "Second page only"
    assert "Page one specification" not in rows[-1].text
    assert rows[0].metadata["document_date"] is None
    assert rows[0].metadata["addendum_number"] is None
    assert rows[0].metadata["bid_id"] == str(first.bid_id)
    assert rows[0].id != other_id(second, rows[0])
    json.dumps(rows[0].metadata)


def other_id(source: ChunkDocument, row) -> object:
    from app.ingestion.chunking.base import chunk_identity

    return chunk_identity(source.document_id, row.chunk_index, row.content_hash)


def test_reprocessing_is_idempotent_and_changes_replace_one_document() -> None:
    source = _document()
    other = _document()
    pipeline, store = _pipeline()
    original = _parsed(source.document_id, [ParsedSection(page_number=1, text="Original specification text")])
    changed = _parsed(source.document_id, [ParsedSection(page_number=1, text="Revised specification text")])
    untouched = _parsed(other.document_id, [ParsedSection(page_number=1, text="Other document")])

    first = asyncio.run(pipeline.persist(source, original))
    stored_id = store.rows[source.document_id][0].id
    second = asyncio.run(pipeline.persist(source, original))
    unchanged_id = store.rows[source.document_id][0].id
    asyncio.run(pipeline.persist(other, untouched))
    kept_id = store.rows[other.document_id][0].id
    third = asyncio.run(pipeline.persist(source, changed))

    assert first.chunks_created == 1
    assert second.chunks_created == 0
    assert second.chunks_skipped == 1
    assert unchanged_id == stored_id
    assert third.chunks_created == 1
    assert store.rows[source.document_id][0].id != stored_id
    assert store.rows[source.document_id][0].text == "Revised specification text"
    assert store.rows[other.document_id][0].id == kept_id
    assert store.rows[other.document_id][0].text == "Other document"


def test_table_chunks_keep_headers_and_registered_dates() -> None:
    source = ChunkDocument(
        document_id=uuid4(),
        bid_id=uuid4(),
        file_name="pricing.html",
        file_type="html",
        relative_path="Open_Bid/pricing.html",
        document_date=date(2026, 4, 1),
        addendum_number=2,
    )
    section = ParsedSection(
        section_title="Pricing",
        heading_level=2,
        anchor="pricing",
        text="See the table.",
        tables=[
            ParsedTable(
                table_id="table-1",
                section_title="Pricing",
                anchor="pricing",
                headers=["Part", "Price"],
                rows=[[f"PN-{index}", f"${index}.00"] for index in range(3)],
                text="",
            )
        ],
    )
    pipeline, store = _pipeline(table_chunk_max_rows=2)
    summary = asyncio.run(pipeline.persist(source, _parsed(source.document_id, [section])))

    assert summary.text_chunks_created == 1
    assert summary.table_chunks_created == 2
    tables = [row for row in store.rows[source.document_id] if row.chunk_type == "table"]
    assert tables[0].text.startswith("Part | Price\nPN-0 | $0.00")
    assert tables[1].text == "Part | Price\nPN-2 | $2.00"
    assert tables[0].metadata["table_id"] == "table-1"
    assert tables[0].metadata["heading_path"] == ["Pricing"]
    assert tables[0].metadata["anchor"] == "pricing"
    assert tables[0].metadata["document_date"] == "2026-04-01"
    assert tables[0].metadata["addendum_number"] == 2
    assert tables[0].page_number is None
    json.dumps([row.metadata for row in store.rows[source.document_id]])


def test_multiple_tables_in_one_section_receive_unique_contiguous_indexes() -> None:
    source = _document()
    section = ParsedSection(
        page_number=1,
        section_title="Products",
        text="Two product tables follow.",
        tables=[
            ParsedTable(
                table_id=f"table-{table_index}",
                headers=["Part", "Quantity"],
                rows=[
                    [f"PART-{table_index}-{row_index}", str(row_index)]
                    for row_index in range(3)
                ],
                text="",
            )
            for table_index in range(2)
        ],
    )
    pipeline, store = _pipeline(table_chunk_max_rows=2)

    summary = asyncio.run(
        pipeline.persist(source, _parsed(source.document_id, [section]))
    )
    rows = store.rows[source.document_id]

    assert summary.text_chunks_created == 1
    assert summary.table_chunks_created == 4
    assert [row.chunk_index for row in rows] == list(range(5))
    assert len({row.id for row in rows}) == 5


def test_failed_parse_does_not_replace_existing_chunks() -> None:
    source = _document()
    pipeline, store = _pipeline()
    asyncio.run(
        pipeline.persist(source, _parsed(source.document_id, [ParsedSection(page_number=1, text="Keep me")]))
    )
    failed = _parsed(
        source.document_id,
        [],
        status=ParseStatus.FAILED,
        errors=[
            ParseMessage(
                code="no_extractable_text",
                message="PDF has no extractable text. OCR may be needed later.",
            )
        ],
    )
    summary = asyncio.run(pipeline.persist(source, failed))

    assert summary.chunks_created == 0
    assert summary.errors[0].code == "no_extractable_text"
    assert store.rows[source.document_id][0].text == "Keep me"


def test_parsed_pdf_and_html_flow_into_chunks(tmp_path) -> None:
    pdf_path = tmp_path / "notes.pdf"
    write_text_pdf(pdf_path, ["Visible specification text"])
    write_table_pdf(tmp_path / "pricing.pdf")
    html_path = tmp_path / "portal.html"
    html_path.write_text(
        "<html><body><h1 id='scope'>Scope</h1><p>Installation requirements.</p>"
        "<table id='parts'><tr><th>Part</th><th>Qty</th></tr><tr><td>PN-100</td><td>2</td></tr></table>"
        "</body></html>",
        encoding="utf-8",
    )
    pdf_source = RegisteredSource(uuid4(), "notes.pdf", "notes.pdf", "pdf")
    table_source = RegisteredSource(uuid4(), "pricing.pdf", "pricing.pdf", "pdf")
    html_source = RegisteredSource(uuid4(), "portal.html", "portal.html", "html")
    pdf = PdfParser().parse(pdf_path, pdf_source)
    table_pdf = PdfParser().parse(tmp_path / "pricing.pdf", table_source)
    html = HtmlParser().parse(html_path, html_source)
    pipeline, store = _pipeline()

    pdf_summary = asyncio.run(pipeline.persist(_as_chunk_doc(pdf_source, "pdf"), pdf))
    table_summary = asyncio.run(pipeline.persist(_as_chunk_doc(table_source, "pdf"), table_pdf))
    html_summary = asyncio.run(pipeline.persist(_as_chunk_doc(html_source, "html"), html))

    assert pdf_summary.text_chunks_created == 1
    assert store.rows[pdf_source.document_id][0].page_number == 1
    assert "Visible specification text" in store.rows[pdf_source.document_id][0].text
    assert table_summary.table_chunks_created == 1
    assert "Part | Qty" in store.rows[table_source.document_id][0].text or any(
        "Widget" in row.text for row in store.rows[table_source.document_id]
    )
    assert html_summary.text_chunks_created == 1
    assert html_summary.table_chunks_created == 1
    html_rows = store.rows[html_source.document_id]
    assert html_rows[0].metadata["heading_path"] == ["Scope"]
    assert html_rows[0].metadata["anchor"] == "scope"
    assert any(row.chunk_type == "table" and "PN-100" in row.text for row in html_rows)


def _as_chunk_doc(source: RegisteredSource, file_type: str) -> ChunkDocument:
    return ChunkDocument(
        document_id=source.document_id,
        bid_id=uuid4(),
        file_name=source.file_name,
        file_type=file_type,
        relative_path=source.relative_path,
    )


def test_database_failure_rolls_back_the_document_being_replaced() -> None:
    repository = SqlChunkRepository(_ChunkSession())
    source = _document()
    pipeline = ChunkingPipeline(
        repository,
        chunk_size=1000,
        chunk_overlap=0,
        table_chunk_max_rows=20,
        chunk_min_text_length=50,
    )
    asyncio.run(
        pipeline.persist(source, _parsed(source.document_id, [ParsedSection(page_number=1, text="Original row")]))
    )
    other = _document()
    asyncio.run(
        pipeline.persist(other, _parsed(other.document_id, [ParsedSection(page_number=1, text="Leave this row")]))
    )
    repository._session.fail_flush = True

    with pytest.raises(OperationalError):
        asyncio.run(
            pipeline.persist(
                source,
                _parsed(source.document_id, [ParsedSection(page_number=1, text="Replacement row")]),
            )
        )

    stored = repository._session.chunks
    assert [row.text for row in stored if row.document_id == source.document_id] == ["Original row"]
    assert [row.text for row in stored if row.document_id == other.document_id] == ["Leave this row"]


class _Savepoint:
    def __init__(self, session: "_ChunkSession") -> None:
        self._session = session
        self._chunks = list(session.chunks)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is not None:
            self._session.chunks = self._chunks
        return False


class _ChunkSession:
    def __init__(self) -> None:
        self.chunks: list[DocumentChunk] = []
        self.fail_flush = False

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self)

    def add(self, obj: DocumentChunk) -> None:
        self.chunks.append(obj)

    async def flush(self) -> None:
        if self.fail_flush:
            raise OperationalError("INSERT", {}, Exception("connection closed"))

    async def scalars(self, statement):
        document_id = statement.whereclause.right.value
        rows = [row for row in self.chunks if row.document_id == document_id]
        return sorted(rows, key=lambda row: row.chunk_index)

    async def execute(self, statement) -> None:
        document_id = statement.whereclause.right.value
        self.chunks = [row for row in self.chunks if row.document_id != document_id]
