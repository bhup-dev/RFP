# RFP Intelligence Platform

This platform reads procurement bid documents (PDF and HTML), indexes them for hybrid search, and runs a LangGraph multi-agent workflow over them:

- It extracts 20 required fields, each with citations and a confidence score.
- It compares addenda with the original solicitation.
- It validates every value against its sources and routes doubtful values to human review.
- It answers questions about a bid, with citations.

You can use it through a FastAPI backend, a React UI, or a command-line interface.

| Folder | Contents |
|---|---|
| `backend/` | FastAPI app, ingestion pipeline, search engine, LangGraph agents, CLI, scripts, tests. Detailed reference: [`backend/README.md`](backend/README.md) |
| `frontend/` | React + Vite UI (JavaScript/JSX): bids, uploads, processing jobs, reviews, comparison, chat |
| `deliverables/` | Assignment outputs for Bid1 (Dallas ISD RFP JA-207652, Student and Staff Computing Devices) and Bid2 (Maryland PORFP #E20P4600040, Dell laptops): per-bid JSON, retrieval evaluation, Q&A log, agent trace, architecture diagram. Index: [`deliverables/README.md`](deliverables/README.md) |

## Setup

**Requirements:**

- Python 3.11 or newer (developed on 3.12)
- PostgreSQL 13 or newer
- Qdrant (Docker image `qdrant/qdrant`)
- Node.js 20 or newer for the UI
- An OpenAI API key

```bash
# Services
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant
psql -h localhost -d postgres -c "CREATE DATABASE rfp_intelligence"

# Backend
cd backend
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # set DATABASE_URL and OPENAI_API_KEY
alembic upgrade head
python -c "from app.services.qdrant_service import QdrantService; QdrantService().ensure_collection()"

# Frontend
cd ../frontend
npm install
cp .env.example .env
```

**Backend dependencies** (`backend/requirements.txt`):

- FastAPI and Uvicorn
- SQLAlchemy (async) with asyncpg, and Alembic
- qdrant-client
- openai
- langgraph
- PyMuPDF, BeautifulSoup and lxml
- pytest

Optional: `sentence-transformers` is needed only if you enable reranking. **Frontend:** React 19, react-router, Vite, Vitest.

Never commit `backend/.env`. It holds the API key and is gitignored.

## How to run each mode

All backend commands run from `backend/` with the virtual environment active.

**API and UI**

```bash
uvicorn app.main:app --reload          # http://127.0.0.1:8000, background worker included
cd ../frontend && npm run dev          # http://localhost:5173, proxies /api to the backend
```

In the UI, create a bid on the **New bid** page, upload its documents and start **Process & analyze**. Processing runs as a background job (ingestion, then the agent workflow). You can then open the bid's profile, reviews, comparison and chat pages.

**Standalone worker:** set `JOB_WORKER_EMBEDDED=false` on the API, then run `python -m app.worker`. Read the checkpointer limitation below first.

**CLI** (`python -m app.cli`). Every command prints JSON; add `--output FILE` to save it.

```bash
python -m app.cli ingest --folder FOLDER_NAME [--bid ID]   # register, parse, chunk, embed a folder under data/bids
python -m app.cli process BID_ID                           # run the full LangGraph workflow
python -m app.cli profile BID_ID                           # stored 20-field profile with citations and confidence
python -m app.cli search BID_ID "warranty requirements" --top-k 5
python -m app.cli ask BID_ID "What is the submission deadline?" [--compare OTHER_BID_ID]
python -m app.cli trace BID_ID                             # trace of the latest workflow run
```

**HTTP API.** Every route is under `/api/v1`. The main ones:

- `POST /bids`, `POST /bids/{id}/documents`, `POST /bids/{id}/process`
- `POST /search`
- `POST /bids/{id}/ask`
- `GET /bids/{id}/intelligence-profile`
- `POST /bids/compare`
- `GET /reviews`

The full list is in `backend/README.md`.

**Evaluation and deliverable scripts**

```bash
python -m scripts.export_bid_outputs BID1_ID BID2_ID --names Bid1 Bid2 --output-dir ../deliverables/bid_outputs
python -m scripts.build_retrieval_ground_truth BID1_ID BID2_ID    # labels -> evaluation/retrieval_queries.json
python -m scripts.retrieval_eval                                  # -> deliverables/retrieval_evaluation/
python -m scripts.chat_eval BID1_ID BID2_ID --output ../deliverables/qa_log/qa_log.json
python -m scripts.format_qa_log ../deliverables/qa_log/qa_log.json --primary-label "Bid1 - ..." --secondary-label "Bid2 - ..."
python -m app.cli trace BID1_ID --output ../deliverables/agent_trace/Bid1_extraction_trace.json
python -m scripts.format_trace ../deliverables/agent_trace/Bid1_extraction_trace.json --label "Bid1 (...)"
python -m app.evaluation.runner --mode mock                       # synthetic regression suite
```

**Tests**

```bash
cd backend && pytest -q -m "not integration and not qdrant"   # unit tests, no services needed
pytest -q                                                     # everything, including integration tests
cd ../frontend && npm test
```

## Design decisions

### Ingestion and chunking

- **Parsing.** PDFs are parsed with PyMuPDF and HTML with BeautifulSoup and lxml. Each PDF page and each HTML heading section is a unit, so every chunk keeps an exact page number or section for its citation. Image-only PDFs are marked `needs_ocr`; no OCR engine is included.
- **Text chunks.** Text is split into 1,000-character chunks with 150 characters of overlap (`CHUNK_SIZE`, `CHUNK_OVERLAP`). A split prefers a paragraph break, then a sentence break, then whitespace, and a chunk never crosses a page or section. Character counts were chosen over tokens to keep chunking independent of the model. 1,000 characters is roughly one requirement clause with its context, and the overlap keeps sentences that sit on a boundary findable.
- **Table chunks.** Tables are chunked separately, at most 20 rows per chunk with the header row repeated, and a row is never split. Specification and pricing tables stay readable as tables, and a single row (for example one device's specification) is always complete.
- **Chunk ids** are UUID5 values derived from the document, the position and the text hash. Re-ingesting unchanged files therefore changes nothing. Ids are stable, so citations, Qdrant points and evaluation labels all refer to the same chunk.
- **Storage.** PostgreSQL is the source of truth for documents, chunks and every run. Qdrant stores vectors plus the payload needed to filter and cite. Duplicate files are detected by SHA-256 hash.

### Embedding model

`text-embedding-3-small` (1536 dimensions, cosine distance in Qdrant). It is cheap enough to re-embed a whole bid, works well on formal English procurement text, and uses the same provider as the LLM, so there is one API key. The model and dimension are settings, and a mismatch with the Qdrant collection is rejected rather than silently resized.

### Retrieval strategy

- **Hybrid search.** Semantic search (Qdrant) and keyword search (PostgreSQL full text plus exact matching of identifiers such as `JA-207652` or `WD22TB4`) run separately. Reciprocal Rank Fusion with k = 60 combines them.
  - RRF needs no score calibration between the two very different scorers.
  - Keyword matching protects exact identifiers that embeddings can blur.
- **Optional extras:**
  - LLM query rewriting (the original query is always searched first, and a variant that drops an identifier, date or number is rejected).
  - Cross-encoder reranking (`BAAI/bge-reranker-base`).
  - If one backend is down, search continues in degraded mode with a warning.
- **Citation check.** Every returned chunk id is checked against PostgreSQL before it is used.
- **Measured results.** The details are in [`deliverables/retrieval_evaluation/report.md`](deliverables/retrieval_evaluation/report.md): 41 labelled questions over two bids.
  - Hybrid RRF scored best (Recall@5 0.804, MRR 0.764), slightly ahead of semantic only (0.787 / 0.752).
  - Keyword search alone scored very low, because its query parser requires every word of a question to appear in one chunk.
  - LLM rewriting added about 1.5 s per query and did not improve the results.
  - Reranking was not measured because its runtime is not installed.

### Agent framework

**LangGraph `StateGraph`** (`app/agents/workflow.py`). It was chosen over a free-form agent loop because the workflow is a fixed, auditable pipeline:

- **Explicit nodes and conditional failure edges.** Any failed stage goes to `finalize`, so a failed stage can never yield a "successful" profile.
- **`interrupt()` for human review.**
- **A typed state** that carries only ids and summaries.

The graph runs 13 nodes in this order:

1. Context check.
2. Field extraction.
3. Addendum analysis.
4. Four domain agents: requirements, product specification, commercial terms, and compliance and documentation.
5. A deterministic evidence-review agent.
6. Validation.
7. Review-item creation.
8. The human-review gate.
9. Profile generation and finalize.

A separate graph compares bids.

**Field extraction in groups.** The 20 fields are split into seven groups (identity, schedule, terms, delivery, documents, products, summary). Each group has its own search queries and evidence budget and its own model call, and the calls run concurrently. The first version used one evidence pool and one call for all 20 fields. Its sequential searches filled the 60-chunk budget before the contact and summary queries ran, so the buyer contact, pre-bid meeting and Bid Summary were missed even though the documents state them. If a group's claim breaks the output contract (for example a value marked `not_found`), the group is retried once with the errors listed. A claim that still breaks it fails only that field, not the whole run.

**Validation with a typed verdict.** The semantic validator prompt states the exact JSON keys, types and allowed status words. Without them, gpt-4o-mini returned words such as `"supported"` and `"direct"` where the schema expects an enum and numbers, so every semantic check failed and fell back to `needs_review`. Date, submission-type and identifier fields are also checked semantically, because a literal match only proves that the string appears in the source, not that it answers the field. For example, a line-item date is not a delivery date. The fix was measured on the same two bids: validated fields went from 2 to 13 (Bid1) and from 5 to 15 (Bid2).

**How the agents communicate.** Agents never call each other. Each node writes its run and findings to PostgreSQL and passes the run id through the graph state, and later nodes load earlier results by id. This gives every result a durable, queryable history, and lets a trace be rebuilt after the fact (see [`deliverables/architecture.md`](deliverables/architecture.md)). Every node also writes a `workflow_node_events` row with its timing, status, error category and run ids.

### Prompts

| Stage | Prompt |
|---|---|
| Field extraction | `app/services/extraction_service.py` `SYSTEM_PROMPT` |
| Addendum analysis | `app/services/addendum_analysis_service.py` `DISCOVERY_SYSTEM_PROMPT`, `COMPARISON_SYSTEM_PROMPT` |
| Specialized agents | `app/agents/specialized.py` `AGENT_DEFINITIONS` |
| Validation | `app/services/extraction_validation_service.py` `SEMANTIC_SYSTEM_PROMPT` |
| Chat | `app/services/bid_qa_service.py` `SYSTEM_PROMPT` |
| Query rewriting | `app/retrieval/query_rewriter.py` |

All the prompts follow the same rules:

- Every prompt that sees document text starts with `UNTRUSTED_DOCUMENT_POLICY` (`app/core/prompt_security.py`): document text is data, never instructions.
- The model must answer in structured JSON validated with Pydantic, and must cite the supplied chunk ids.
- Any cited id that was not supplied, that belongs to another bid, or that does not exist is rejected in code. A value is never accepted because the model said so.
- Missing information must be returned as null, never guessed.

### Confidence and validation

Confidence is a transparent heuristic, not a calibrated probability:

\[
0.30\,\text{citation integrity} + 0.30\,\text{directness} + 0.20\,\text{completeness} + 0.20\,\text{source/value agreement}
\]

- Invalid citations cap the score at 0.20, and unresolved conflicts cap it at 0.35.
- Categories: high ≥ 0.80, medium ≥ 0.55.
- Low-confidence, unsupported, conflicting or missing critical fields become review items.

## Known limitations

- **Checkpoints are in memory.** LangGraph uses `InMemorySaver`. A workflow paused for human review cannot resume after a restart or on another worker, while PostgreSQL keeps the durable history.
- **Keyword search on its own** returns results for few natural-language questions (see the retrieval report).
- **Reranking** needs `sentence-transformers`, which is not installed by default, so it has not been evaluated.
- **No OCR.** Image-only PDFs are flagged and not read.
- **No user authentication** on the API, apart from a shared token for review endpoints. Run it on a trusted network.
- **The LLM is not deterministic.** Re-running extraction can give different values, and every run is kept as history.
- **Semantic validation is only as good as the model.** In the Bid1 output, gpt-4o-mini extracted and validated a prime-to-subcontractor payment clause as "Payment Terms" despite an explicit prompt rule (see the known issues in [`deliverables/README.md`](deliverables/README.md)). "Validated" means the model and the deterministic checks agreed, not that a human checked the value.
- **Model used for the deliverables.** They were produced with `gpt-4o-mini` (the local `.env`), while `.env.example` suggests `gpt-5.5`.
