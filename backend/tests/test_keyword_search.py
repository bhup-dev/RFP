"""PostgreSQL keyword statements are bounded, filtered, and parameterized."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.retrieval.keyword_search import KeywordSearch, identifier_terms, keyword_statement
from app.retrieval.query_service import prepare_query
from app.schemas.retrieval import RetrievalRequest


class Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class Session:
    def __init__(self, rows=()):
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return Result(self.rows)


def test_identifier_terms_generalize_to_procurement_ids() -> None:
    assert identifier_terms("WD22TB4") == ("wd22tb4",)
    assert identifier_terms("CC7802 BPM044557") == ("cc7802", "bpm044557")
    assert identifier_terms("contract JA-207652") == ("ja-207652",)
    assert identifier_terms("ordinary payment terms") == ()


def test_keyword_statement_is_parameterized_and_filtered() -> None:
    bid_id, document_id = uuid4(), uuid4()
    query = prepare_query(
        RetrievalRequest(
            query="JA-207652'; DROP TABLE bids; --",
            bid_id=bid_id,
            document_ids=[document_id],
            file_types=["pdf"],
            chunk_types=["table"],
            keyword_candidates=17,
        )
    )
    statement = keyword_statement(query)
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": False},
    )
    rendered = str(compiled)
    assert "websearch_to_tsquery" in rendered
    assert "LIMIT" in rendered
    assert "DROP TABLE" not in rendered
    assert "documents.bid_id" in rendered
    assert "documents.id IN" in rendered
    assert "documents.document_type IN" in rendered
    assert "document_chunks.chunk_type IN" in rendered
    assert compiled.params["search_query"] == query.text


def test_keyword_results_preserve_citation_metadata() -> None:
    bid_id, document_id, chunk_id = uuid4(), uuid4(), uuid4()
    chunk = SimpleNamespace(
        id=chunk_id,
        document_id=document_id,
        chunk_index=2,
        page_number=5,
        section_title="Products",
        chunk_type="table",
        text="CC7802 | 4",
        content_hash="abc",
        chunk_metadata={"heading_path": ["Products"], "table_id": "table-2"},
    )
    document = SimpleNamespace(
        id=document_id,
        bid_id=bid_id,
        file_name="products.pdf",
        document_type="pdf",
        document_date=None,
        addendum_number=None,
    )
    bid = SimpleNamespace(
        id=bid_id,
        bid_identifier="Bid1",
        solicitation_number="JA-207652",
    )
    session = Session([(chunk, document, bid, 1.5)])

    result = asyncio.run(
        KeywordSearch(session).search(
            prepare_query(RetrievalRequest(query="CC7802"))
        )
    )

    candidate = result.candidates[0]
    assert candidate.chunk_id == chunk_id
    assert candidate.keyword_score == 1.5
    assert candidate.metadata["file_type"] == "pdf"
    assert candidate.metadata["table_id"] == "table-2"
    assert candidate.metadata["solicitation_number"] == "JA-207652"
