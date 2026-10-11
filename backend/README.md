# RFP Intelligence Platform — Backend

Python API for the RFP Intelligence Platform. This setup provides the FastAPI application, configuration, a health check, the PostgreSQL schema, Qdrant collection access, and source-file registration.

Requires **Python 3.11 or newer**.

## Virtual environment

From the `backend` directory.

macOS and Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Use `python3.11` or `python3` if that command points at Python 3.11 or newer.

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

## Install dependencies

With the virtual environment activated:

```bash
pip install -r requirements.txt
```

The same command works on macOS, Linux, and Windows.

## Environment configuration

Configuration is read from environment variables. An optional `.env` file in the `backend` directory is loaded when it exists. The API starts with built-in defaults, so a `.env` file is not required.

```bash
cp .env.example .env
```

Windows (Command Prompt):

```cmd
copy .env.example .env
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `RFP Intelligence Platform` | Application title |
| `APP_VERSION` | `0.1.0` | Application version |
| `ENVIRONMENT` | `development` | Runtime environment name. `production` selects JSON logs when `LOG_FORMAT=auto` |
| `DEBUG` | `false` | Adds source location to text logs. API responses stay free of tracebacks |
| `API_PREFIX` | `/api` | Prefix reserved for future API routes |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `LOG_FORMAT` | `auto` | `text`, `json`, or `auto` |
| `REQUEST_TIMEOUT_SECONDS` | `60` | Timeout for LLM, embedding, and Qdrant client calls |
| `HEALTH_CHECK_TIMEOUT_SECONDS` | `2` | Timeout for PostgreSQL and Qdrant readiness checks |
| `STORAGE_PATH` | `storage` | Directory created at startup for later ingestion. Relative to `backend/` |
| `INGESTION_INPUT_ROOT` | `data/bids` | Folder scanned for PDF and HTML files. Relative to `backend/`. Not created at startup |
| `CHUNK_SIZE` | `1000` | Maximum narrative chunk size, in characters |
| `CHUNK_OVERLAP` | `150` | Characters repeated from the previous narrative chunk. Must be smaller than `CHUNK_SIZE` |
| `TABLE_CHUNK_MAX_ROWS` | `20` | Maximum data rows in one table chunk. Headers are repeated on each group |
| `CHUNK_MIN_TEXT_LENGTH` | `50` | Configured floor. Whitespace-only text is skipped. Shorter real text is kept |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/rfp_intelligence` | Async SQLAlchemy URL for PostgreSQL |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant HTTP endpoint |
| `QDRANT_API_KEY` | empty | Optional API key. Leave blank for local Qdrant |
| `QDRANT_COLLECTION_NAME` | `rfp_document_chunks` | Collection that stores chunk embeddings |
| `LLM_PROVIDER` | `openai` | Completion provider. `openai` is supported |
| `LLM_MODEL` | `gpt-4o-mini` | Model used for text generation |
| `OPENAI_API_KEY` | empty | OpenAI key. Leave blank to start the API |
| `EMBEDDING_PROVIDER` | `openai` | Embedding provider. `openai` is supported |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model. Independent of `LLM_MODEL` |
| `EMBEDDING_DIMENSION` | `1536` | Vector size. Must match the model and the Qdrant collection |
| `EMBEDDING_BATCH_SIZE` | `32` | Chunks loaded, embedded, and upserted together. From 1 to 256 |
| `SEARCH_RRF_CONSTANT` | `60` | Reciprocal Rank Fusion constant. Ranks begin at 1 |
| `SEARCH_ALLOW_DEGRADED` | `true` | Return one healthy backend with an explicit degraded mode |
| `QUERY_REWRITING_ENABLED` | `true` | Generate bounded LLM search variants while preserving the original |
| `QUERY_REWRITE_MAX_VARIANTS` | `3` | Maximum generated alternatives, from 1 to 5 |
| `QUERY_REWRITE_TIMEOUT_SECONDS` | `10` | LLM rewrite timeout before original-query fallback |
| `RERANKING_ENABLED` | `false` | Enable optional local cross-encoder scoring |
| `RERANKER_PROVIDER` | `sentence_transformers` | Optional reranker adapter |
| `RERANKER_MODEL` | `BAAI/bge-reranker-base` | Cross-encoder loaded lazily on first rerank |
| `RERANK_CANDIDATE_LIMIT` | `30` | Maximum RRF candidates scored by the cross-encoder |
| `RERANK_BATCH_SIZE` | `8` | Query-passage pairs per model batch |
| `RERANK_TIMEOUT_SECONDS` | `15` | Reranking timeout before retaining RRF order |
| `RERANK_MAX_INPUT_CHARACTERS` | `8000` | Model-input character limit; beginning and end are retained |
| `EXTRACTION_QUERY_TOP_K` | `12` | Evidence chunks retained from each field-group search |
| `EXTRACTION_MAX_EVIDENCE_CHUNKS` | `60` | Maximum distinct chunks supplied to one extraction request |
| `EXTRACTION_MAX_OUTPUT_TOKENS` | `8000` | Maximum structured extraction response size |
| `ADDENDUM_MAX_CHUNKS_PER_DOCUMENT` | `30` | Addendum chunks inspected during topic discovery |
| `ADDENDUM_RELATED_TOP_K` | `8` | Earlier chunks retained for each discovered topic |
| `ADDENDUM_MAX_EVIDENCE_CHUNKS` | `60` | Maximum distinct earlier chunks used per addendum |
| `ADDENDUM_MAX_OUTPUT_TOKENS` | `8000` | Maximum response size for each addendum LLM phase |
| `VALIDATION_SEMANTIC_ENABLED` | `true` | Use bounded semantic checks when deterministic evidence is insufficient |
| `VALIDATION_HIGH_CONFIDENCE_THRESHOLD` | `0.80` | Heuristic high-confidence category threshold |
| `VALIDATION_MEDIUM_CONFIDENCE_THRESHOLD` | `0.55` | Heuristic medium-confidence category threshold |
| `VALIDATION_MAX_OUTPUT_TOKENS` | `2000` | Maximum structured semantic-verifier response size |

The database URL in `.env.example` is an example. Use a local user and database name that match your PostgreSQL install. The password is not written into migration files, and application logs do not print the URL.

Do not put secrets in `.env.example`. `.env` is gitignored.

Importing the API does not connect to PostgreSQL, Qdrant, or OpenAI. Startup validates provider names, log settings, and the embedding dimension, then creates `STORAGE_PATH`. It does not connect to those services and does not require `OPENAI_API_KEY`. A database connection is opened only when a session, a readiness check, or an Alembic command needs one. A Qdrant connection is opened only when a Qdrant operation or a Qdrant health check runs. An OpenAI client is created only when a completion or embedding method runs, and only when `OPENAI_API_KEY` is set.

The process listens on `http://127.0.0.1:8000` when started with the uvicorn command below. Host and port are uvicorn arguments.

## Logging and request IDs

`configure_logging` runs when the process starts. Development logs are single lines with a UTC timestamp, level, logger name, request id, and message. `ENVIRONMENT=production` with `LOG_FORMAT=auto` writes one JSON object per line so a log collector can parse them. `LOG_FORMAT=text` or `LOG_FORMAT=json` overrides that choice.

Each HTTP response includes `X-Request-ID`. Send that header with a value of 8 to 128 letters, digits, dots, underscores, or hyphens to keep your own id. Any other value is replaced with a UUID. The same id is attached to logs for that request and is cleared when the request ends, including when the request fails. Concurrent requests keep separate ids.

`bind_operation_id` in `app.core.context` reserves an operation id for a later extraction run. Document registration does not bind one.

Log messages are passed through a redaction filter. Database URL passwords, `OPENAI_API_KEY`, and `QDRANT_API_KEY` are replaced before the record is written. Prompts and document text are not logged by the provider clients.

## PostgreSQL

PostgreSQL 13 or newer is required. `gen_random_uuid()` is used for primary keys. Install and start PostgreSQL locally, then create an empty database:

macOS and Linux:

```bash
psql -h localhost -p 5432 -d postgres -c "CREATE DATABASE rfp_intelligence"
```

Windows (PowerShell), from a shell where `psql` is on `PATH`:

```powershell
psql -h localhost -p 5432 -d postgres -c "CREATE DATABASE rfp_intelligence"
```

Point `DATABASE_URL` at that database. The driver must be `postgresql+asyncpg`. Copy `.env.example` to `.env` and edit the URL before applying migrations.

## Migrations

From the `backend` directory, with the virtual environment activated. Review a generated migration before applying it. After model changes, generate a new revision instead of editing a migration that has already been applied:

```bash
alembic revision --autogenerate -m "initial database schema"
alembic upgrade head
alembic current
alembic history
```

The same commands work on macOS, Linux, and Windows. Alembic reads `DATABASE_URL` from the application settings. The initial revision is in `alembic/versions/`.

## Qdrant

Qdrant stores chunk embeddings and citation metadata. PostgreSQL remains the source of truth for documents and chunks. This setup does not generate embeddings or ingest documents.

Start Qdrant locally with the official image. Docker is required for this command. macOS, Linux, and Windows (Docker Desktop):

```bash
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant
```

Create the configured collection once. The command does nothing if the collection already exists, and it does not delete an existing collection:

```bash
python -c "from app.services.qdrant_service import QdrantService; QdrantService().ensure_collection()"
```

The API process does not create the collection on startup. Check connectivity and collection readiness:

```bash
curl -i http://127.0.0.1:8000/health/qdrant
```

Windows (PowerShell):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/qdrant
```

A ready collection returns HTTP 200 and `"status": "ok"`. If Qdrant is down or the collection has not been created, the response is HTTP 503 and `"status": "unavailable"`. `GET /health` stays available either way.

`EMBEDDING_DIMENSION` must match `EMBEDDING_MODEL` and the vector size stored by `QDRANT_COLLECTION_NAME`. The example pair is `text-embedding-3-small` and `1536`. `text-embedding-3` models can request that output size. Other embedding models return their native size, and a mismatch is rejected.

If the collection already exists at a different size, collection creation, upsert, and search raise an error that names both sizes. Set `EMBEDDING_DIMENSION` to the size the collection already stores, or set `QDRANT_COLLECTION_NAME` to a new name and create that collection at the new size. The existing collection is left unchanged.

## LLM and embeddings

`LLM_PROVIDER=openai` and `EMBEDDING_PROVIDER=openai` are the supported providers. An unsupported name fails configuration validation when the process starts, and the provider call raises `ProviderConfigurationError` as well. The LLM model and the embedding model are separate settings. An API key is checked only when a completion or embedding method runs.

`app.services.llm_service.LLMService.generate_response` sends a user prompt, and an optional system message, to the OpenAI Responses API. `app.services.embedding_service.EmbeddingService.embed_text` and `embed_texts` call the embeddings API. A batch is one request, and the returned vectors stay in input order. Each vector is checked against `EMBEDDING_DIMENSION` before it is returned. Qdrant also checks that length, and the collection's stored size, before it accepts a vector.

Leave `OPENAI_API_KEY` empty to import the application and run the tests. Calling either service without the key raises `ProviderConfigurationError`. The services do not retry provider requests. Timeouts, rate limits, and authentication failures raise `ProviderRequestError`. Those messages omit the API key and the request text.

Document ingestion, retrieval, and agent orchestration are later tasks. These services only generate text and embeddings.

## Start the server

From the `backend` directory, with the virtual environment activated:

```bash
uvicorn app.main:app --reload
```

The same command works on macOS, Linux, and Windows. The server listens on `http://127.0.0.1:8000`.

## Health endpoints

`GET /health` is the liveness check. It reports that the process is running and does not contact PostgreSQL, Qdrant, or a model provider.

```bash
curl -i http://127.0.0.1:8000/health
```

Windows (PowerShell):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

`GET /health/ready` is the readiness check. It runs `SELECT 1` through the existing SQLAlchemy engine and calls the existing Qdrant readiness method. Both use `HEALTH_CHECK_TIMEOUT_SECONDS`. A missing collection or a vector-size mismatch makes Qdrant not ready. The check does not run migrations, create tables, or recreate the collection, and it does not call the LLM or embedding APIs. The response names `postgresql` and `qdrant`. HTTP 200 means both are ready. HTTP 503 means at least one is unavailable. Details omit credentials and tracebacks.

```bash
curl -i http://127.0.0.1:8000/health/ready
```

`GET /health/qdrant` still reports the vector database on its own. See the Qdrant section above.

The response header `X-Request-ID` is present on these routes. Use that value to find the matching log lines.

## Document registration

The scan discovers source files and registers their metadata in PostgreSQL. It does not extract text, chunk documents, create embeddings, or write to Qdrant. A registered document keeps `ingestion_status` of `pending`.

Place bid folders under `INGESTION_INPUT_ROOT`. The default is `backend/data/bids`. The directory is not created on startup. Folder names are examples; any name is accepted, and a file is not rejected because its name lacks words such as `RFP` or `addendum`.

```text
backend/data/bids/
├── Bid1/
│   ├── notes.pdf
│   └── portal_snapshot.html
└── City of Springfield/
    └── attachments/
        └── addendum.htm
```

Supported extensions are `.pdf`, `.html`, and `.htm`, compared without regard to case. Other files, including `.txt` and `.docx`, are reported as unsupported and are not registered. Hidden files, `.DS_Store`, `Thumbs.db`, `desktop.ini`, and the directories `.git`, `.venv`, `__pycache__`, `__MACOSX`, and `node_modules` are skipped. Symbolic links that resolve outside the input root are rejected, and directory links are not followed.

The first folder under the input root is the internal `bid_identifier`. A file placed directly in the input root uses that root folder's name. This value is only a grouping id. The scan does not copy it into `solicitation_number`, `title`, or `issuing_organization`, and it does not read an addendum number or document date from the filename. A request can pass `bid_identifier` to override the folder name for that scan. That override is still the internal grouping id.

Duplicate detection uses a streaming SHA-256 hash. The same hash at another path stays one document and keeps the first source path. Running the scan again on an unchanged file reports a duplicate and does not insert another row. If the bytes at an existing path change, the scan reports `changed` and leaves the stored hash and path as they were. `documents` has no unique constraint on the hash or the path, so two scans that insert the same new file at the same moment can each insert a row. A later scan treats either row as the existing document.

`files_discovered` counts every path in `results`. It is the sum of `files_registered`, `duplicates_skipped`, `changed_files`, `unsupported_files`, and `failed_files`.

The endpoint below has no authentication. Use it on a local machine or another trusted network. It accepts a directory relative to `INGESTION_INPUT_ROOT` and rejects absolute paths and `..`. Responses contain source paths relative to that root, not absolute host paths.

From the `backend` directory, with the API running:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ingestion/scan \
  -H "Content-Type: application/json" \
  -d "{}"
```

Scan one folder, or set the internal grouping id for that request:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ingestion/scan \
  -H "Content-Type: application/json" \
  -d "{\"relative_directory\": \"Bid1\", \"bid_identifier\": \"internal-group\"}"
```

Windows (PowerShell):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ingestion/scan -Method Post -ContentType "application/json" -Body "{}"
```

`data/` is gitignored. Do not commit source bid files.

## Document parsing

Parsing is a separate step from registration. It reads a registered PDF or HTML file and returns text, tables, warnings, and errors in the response. The result is not written to PostgreSQL, `document_chunks`, or Qdrant. `ingestion_status` stays `pending`. Chunking and embeddings come later.

The parsers are PyMuPDF (`pymupdf`), BeautifulSoup (`beautifulsoup4`), and lxml. They are installed with the rest of the requirements. No OCR engine is included.

PDF page numbers are one-based: the first page is page 1. Each page is its own section, and the combined `text` joins those pages with a form feed. HTML has no page numbers. A heading becomes a section, and `anchor` is the element id when the file has one.

A table has `table_id`, `headers` when a header row is detectable, and `rows`. A missing cell is null. `text` is the same cells joined with ` | `. Warnings such as `ambiguous_table_span` stay on that table. An empty PDF page in an otherwise readable file produces an `empty_page` warning. A PDF with no extractable text has status `failed`, `needs_ocr` set, and an error saying OCR may be needed later. Encrypted and malformed PDFs also have status `failed`. Warnings and errors are separate lists.

PDF text follows PyMuPDF reading order, so a multi-column page can be imperfect. Ruled tables are extracted. A table drawn only as spaced text may remain in the page text without becoming a table. HTML is read from disk. Scripts, styles, and navigation elements are dropped. Links and images are not fetched.

The parse route has no authentication, like the scan route. Replace the id with one returned by a scan:

```bash
curl -s -X POST \
  http://127.0.0.1:8000/api/v1/ingestion/documents/DOCUMENT_ID/parse
```

Windows (PowerShell):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ingestion/documents/DOCUMENT_ID/parse -Method Post
```

Parser tests use generated files:

```bash
pytest -q tests/test_pdf_parser.py tests/test_html_parser.py tests/test_parse_stage.py tests/test_parse_api.py
```

## Document chunking

Chunking reads parser output and stores rows in `document_chunks`. It does not embed those rows or send them to Qdrant. `ingestion_status` stays `pending`.

Narrative text is split by character count. `CHUNK_SIZE` is not a token count. The splitter prefers a paragraph break, then a sentence break, then whitespace, and otherwise cuts at the character limit. The cut is not a deletion: the next chunk in the same page or section starts `CHUNK_OVERLAP` characters earlier. Pages and HTML sections are chunked separately, so a chunk does not borrow text from the next page. Each piece keeps that page number, heading path, and anchor.

Tables stay tabular. Each chunk repeats the header row when headers were detected, then a group of at most `TABLE_CHUNK_MAX_ROWS` data rows. Empty cells stay empty. Missing headers are omitted rather than invented. A row is not split across chunks, so a wide table chunk can be longer than `CHUNK_SIZE`. Ambiguous table warnings from the parser are copied onto the chunk.

Chunk ids are UUID5 values from the document id, chunk index, and SHA-256 of the stored text. The same inputs produce the same id. Two documents do not share an id. Metadata copies `bid_id`, file name, file type, page, section, heading path, table id, and the registered document date and addendum number. Unknown dates and addendum numbers stay null. Folder names are not turned into solicitation numbers.

Saving an unchanged chunk set does not write new rows. A changed set deletes and inserts that document's chunks inside one savepoint. A failure restores the previous set. Chunks for other documents are not deleted. Changing `CHUNK_SIZE` or `CHUNK_OVERLAP` changes the split, so the next save replaces the old set.

```bash
curl -s -X POST \
  http://127.0.0.1:8000/api/v1/ingestion/documents/DOCUMENT_ID/chunk
```

Known limits: text shorter than `CHUNK_MIN_TEXT_LENGTH` is still stored, so a real sentence is not discarded. Whitespace-only sections produce no chunks. A table chunk can be longer than `CHUNK_SIZE` because a row stays intact. A section title longer than 512 characters is kept in chunk metadata and the title column is left empty. A failed parse, including an image-only PDF, does not replace chunks that are already stored. Chunking does not write embeddings.

The route has no authentication. Chunk tests:

```bash
pytest -q tests/test_text_chunker.py tests/test_table_chunker.py tests/test_chunking_pipeline.py tests/test_chunking_api.py
```

The PostgreSQL chunking test is part of `pytest -q -m integration`. It rolls its transaction back.

## Embedding and Qdrant indexing

Indexing reads chunks that are already stored in PostgreSQL. It does not parse files or rebuild chunks. `EmbeddingService` embeds the stored text. `QdrantService` upserts the vectors into `QDRANT_COLLECTION_NAME`. The collection is created only when it is missing, using `EMBEDDING_DIMENSION` and cosine distance. An existing collection with a different size is left unchanged, and indexing stops with `collection_incompatible`. Set `EMBEDDING_DIMENSION` to the stored size, or set `QDRANT_COLLECTION_NAME` to a new collection created at the new size.

`EMBEDDING_BATCH_SIZE` is the number of chunks loaded from PostgreSQL, sent in one embedding request, and upserted together. The point id is the chunk UUID, so running the same chunk again updates that point. Indexing does not compare a previously stored hash, so unchanged text is embedded again. Changed text is embedded from the current chunk row and written to the same point id. Points for other documents are not deleted.

Empty or whitespace-only chunk text is skipped. A vector with the wrong length, or a non-finite value, is not padded and is not upserted. An embedding failure and a Qdrant failure are separate errors and name the chunk id. The provider client does not retry. Call the endpoint again to retry. A retry embeds the document's chunks again and upserts the same point ids.

`ingestion_status` stays `pending` through parsing and chunking. Indexing sets `processing` before the provider call, then:

- `indexed` when every non-empty chunk was upserted
- `partial` when some points were upserted and some chunks failed
- `failed` when no points were upserted and a chunk or the collection check failed

`completed` is not used for indexing. If the process stops after a Qdrant upsert and before the status commit, Qdrant can hold points while PostgreSQL still says `processing`. Retrying updates those same points.

Indexing is synchronous. There is no job queue. A client calling a large bid needs an HTTP timeout long enough for every batch. Each batch still uses `REQUEST_TIMEOUT_SECONDS`.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/indexing/documents/DOCUMENT_ID
curl -s -X POST http://127.0.0.1:8000/api/v1/indexing/bids/BID_ID
```

These routes have no authentication. They do not search the index.

```bash
pytest -q tests/test_indexing_pipeline.py tests/test_indexing_api.py
```

The PostgreSQL indexing test is part of `pytest -q -m integration`. It deletes only a `test-index-` bid created by that run. It does not call OpenAI or delete a Qdrant collection.

## Hybrid retrieval

`POST /api/v1/search` runs semantic and keyword retrieval independently, then combines their ranks with Reciprocal Rank Fusion (RRF). Semantic retrieval embeds the query once with the configured embedding model and queries the existing Qdrant collection. It does not recreate the collection or re-embed document chunks. Keyword retrieval queries PostgreSQL `document_chunks`, which remains authoritative.

Keyword retrieval uses query-time `to_tsvector('simple', text)`, `websearch_to_tsquery`, and `ts_rank_cd`. The current schema has no stored search vector or GIN index, so Task 2.5 adds no migration. This is the simplest correct design for the current corpus. Larger corpora should measure query plans before adding a generated vector and GIN index. Parameterized substring conditions supplement full-text search for identifiers containing letters, digits, hyphens, underscores, periods, or other punctuation. Examples such as `WD22TB4`, `CC7802`, `BPM044557`, and `JA-207652` are recognized by shape rather than hard-coded values.

Both backends apply `bid_id`, `document_ids`, `file_types`, and `chunk_types`. Qdrant uses the top-level fields written during indexing; PostgreSQL filters the joined document and chunk rows. RRF deduplicates by chunk UUID and adds `1 / (SEARCH_RRF_CONSTANT + rank)` for each list. Ties use the best source rank and then chunk UUID. Semantic, keyword, and fusion scores remain separate.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "delivery within thirty days",
    "bid_id": "00000000-0000-0000-0000-000000000001",
    "file_types": ["pdf"],
    "chunk_types": ["text", "table"],
    "top_k": 8,
    "semantic_candidates": 30,
    "keyword_candidates": 30
  }'
```

The response contains citation fields, distinct source scores, `fusion_score`, normalized filters, and `retrieval_mode`. With `SEARCH_ALLOW_DEGRADED=true`, one failed backend returns `semantic_only` or `keyword_only` plus a warning. Two failures return HTTP 503. An empty result from a healthy backend is different from backend failure. Set the flag to `false` when every response must be hybrid.

The evaluation utility in `app.retrieval.evaluation` calculates Recall@K, Precision@K, MRR, Hit Rate@K, and nDCG@K. Ground truth can match an immutable chunk UUID or a stable source locator (`file_name`, optional page, optional section). `evaluation/retrieval_queries.json` holds 41 questions over the processed Bid1 (Dallas ISD JA-207652) and Bid2 (Maryland Dell laptops) documents, labelled from stored chunk text (see `evaluation/README.md`). `python -m scripts.retrieval_eval` measures every configuration and writes `deliverables/retrieval_evaluation/`. Queries with unknown ground truth are reported separately and excluded from averages.

```bash
pytest -q tests/test_semantic_search.py tests/test_keyword_search.py \
  tests/test_retrieval_fusion.py tests/test_retrieval_service.py \
  tests/test_retrieval_evaluation.py tests/test_search_api.py
pytest -q -m integration tests/test_retrieval_integration.py
pytest -q tests/test_hybrid_retrieval_integration.py
```

The hybrid integration test uses a temporary Qdrant collection, rolled-back PostgreSQL rows, and a fake query embedder. It runs only when PostgreSQL and Qdrant are reachable and never calls OpenAI.

Known limitations: PostgreSQL full-text vectors are calculated at query time; substring identifier matching can be slower on a large corpus; semantic retrieval requires the indexed payload format from Task 2.4; there is no extraction or agent workflow.

## Query rewriting and reranking

Query rewriting is enabled by default and uses the existing `LLMService`. The original query is always first and is always sent to both semantic and keyword retrieval. Generated alternatives are JSON-schema validated, normalized, deduplicated, and bounded. Alternatives that remove an exact identifier, date, or number from the original are rejected. Every variant receives the same bid, document, file-type, and chunk-type filters. A timeout, malformed response, missing credentials, or provider error falls back to the original query with a warning.

Results from query variants are deduplicated by chunk UUID. Each backend keeps a chunk's best variant rank, preferring the original query on ties, and the existing semantic-versus-keyword RRF is then applied. The API reports only the number of variants; generated query text is not logged or returned.

Cross-encoder reranking is disabled by default. It is separate from embedding similarity and sets only `reranker_score`, preserving semantic, keyword, and fusion scores. Install the optional runtime before enabling it:

```bash
pip install "sentence-transformers>=3.0"
```

`BAAI/bge-reranker-base` is loaded lazily on the first reranked request and shared by model name. The initial load can download model files and requires substantially more memory and CPU than baseline retrieval; a compatible PyTorch GPU can reduce latency but is not required by this code. No model is downloaded during import or API startup. Unit tests use deterministic fake scorers and require neither this package nor a GPU.

The cross-encoder scores at most `RERANK_CANDIDATE_LIMIT` RRF candidates in batches. Inputs longer than `RERANK_MAX_INPUT_CHARACTERS` keep both their beginning and end so trailing table rows are represented; the response includes a warning, and the returned chunk text is never modified. A model import, load, scoring, or timeout failure retains RRF order and leaves `reranker_score` null.

The response remains backward-compatible and adds `rewriting_applied`, `rewritten_query_count`, `candidate_count`, `reranking_applied`, and `final_result_count`. Reranking always uses the original user question.

The evaluation utility can compare four measured configurations: baseline, rewriting only, reranking only, and both. It records relevance metrics, mean latency, and mean candidate count without filling missing runs. The live comparison is in `deliverables/retrieval_evaluation/report.md`; reranking is reported as not run there because `sentence-transformers` is not installed.

```bash
pytest -q tests/test_query_rewriter.py tests/test_reranker.py \
  tests/test_retrieval_service.py tests/test_retrieval_evaluation.py
```

## End-to-end processing and validation

The end-to-end coordinator calls the existing registration, parser, chunker, embedding, Qdrant, and status services. It does not duplicate those stages. Place arbitrary bid folders under `INGESTION_INPUT_ROOT`, then process one folder:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ingestion/process \
  -H 'Content-Type: application/json' \
  -d '{"relative_directory": "ExampleBid"}'
```

To safely resume a previously registered document:

```bash
curl -s -X POST \
  http://127.0.0.1:8000/api/v1/ingestion/documents/DOCUMENT_UUID/process
```

The coordinator commits registration before document processing so one parse failure does not erase unrelated registrations. A document with chunks but no successful index resumes at indexing. A document already marked `indexed`, with matching source bytes and persisted chunks, skips parsing, embedding, and Qdrant writes. Changed source bytes are rejected until registration handles that changed source; existing chunks and points are not silently overwritten.

Search remains:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"WD22TB4 warranty","bid_id":"BID_UUID","top_k":8}'
```

Public workflow and search responses contain relative source paths or citation metadata, never absolute filesystem paths. Search resolves final chunk IDs against PostgreSQL before returning them. `ConsistencyAuditor` can compare PostgreSQL chunk IDs, document/bid IDs, content hashes, and text with Qdrant payloads without modifying either system.

PostgreSQL and Qdrant cannot share one transaction. Indexing writes Qdrant before committing the final PostgreSQL status. A process interruption can leave points present while status remains `processing`. Retrying is safe because point IDs are chunk UUIDs; the consistency audit reports missing or mismatched points.

```bash
pytest -q tests/test_end_to_end_pipeline.py tests/test_citation_consistency.py \
  tests/test_workflow_api.py
pytest -q -m integration tests/test_end_to_end_integration.py
RUN_LIVE_E2E=1 pytest -q -s tests/test_live_end_to_end.py
```

The live test uses generated source content, the configured PostgreSQL database and embedding provider, and an isolated temporary Qdrant collection. It removes only its `test-live-e2e-*` bid and temporary collection. `evaluation/task_2_7_report.json` records the measured validation from that task; its retrieval metrics are null because it predates the labelled set. Current retrieval metrics are in `deliverables/retrieval_evaluation/`. Generated fixtures are not represented as real evaluation ground truth.

## Structured field extraction

Task 3.1 extracts the assignment's 20 fields from an ingested bid through:

1. eligible documents with status `indexed`, `partial`, or `completed` and persisted chunks
2. six bounded, bid/document-scoped evidence searches covering identity, dates, terms, forms, products, contacts, and addenda
3. one structured call through the existing `LLMService`
4. strict Pydantic validation of all 20 exact external names
5. authoritative PostgreSQL resolution of every claimed chunk id
6. an append-only `extraction_runs` record and 20 `extracted_fields` records

The extraction retrieval path deliberately disables query rewriting and reranking. This avoids an extra LLM call and optional model load while preserving hybrid semantic/keyword retrieval and citation validation. If only one retrieval backend is available, existing degraded-mode policy applies. A non-null model claim is rejected when any cited chunk was not supplied to the model, does not exist, belongs to another bid, or is outside the eligible document set.

Apply migrations before calling the endpoint. Revision `5f33c9a2d410` adds JSONB evidence document ids to each run and authoritative citation metadata to each field.

```bash
alembic upgrade head
```

All fields are returned under their exact assignment names. Missing evidence is represented by a null value and `missing`; conflicts retain separately traceable citations. Repeating the request creates a new historical run and does not overwrite earlier results.

```bash
curl -s -X POST \
  http://127.0.0.1:8000/api/v1/bids/BID_UUID/extract
```

Response shape:

```json
{
  "run_id": "RUN_UUID",
  "bid_id": "BID_UUID",
  "status": "completed",
  "model_name": "gpt-4o-mini",
  "document_ids": ["DOCUMENT_UUID"],
  "started_at": "2026-10-10T18:00:00Z",
  "completed_at": "2026-10-10T18:00:02Z",
  "fields": {
    "Bid Number": {
      "value": "SOL-123",
      "confidence": 0.96,
      "citations": [{
        "bid_id": "BID_UUID",
        "document_id": "DOCUMENT_UUID",
        "chunk_id": "CHUNK_UUID",
        "file_name": "solicitation.pdf",
        "page_number": 1,
        "section_title": "Solicitation",
        "excerpt": "Solicitation number SOL-123",
        "relevance_score": null
      }],
      "notes": "Stated in the solicitation header.",
      "validation_status": "supported"
    }
  }
}
```

The endpoint returns 404 for an unknown bid, 409 when no eligible chunked documents exist, 502 for malformed structured model output, and 503 when retrieval or the model provider is unavailable. Provider keys, prompts, and stack traces are not returned. Unit tests mock both retrieval and the LLM; no live call is required:

```bash
pytest -q tests/test_extraction_service.py tests/test_extraction_api.py
pytest -q -m integration tests/test_extraction_integration.py
```

## Addendum analysis and conflict detection

Task 3.2 compares explicit amendment documents with earlier source evidence one document at a time. It does not replace or delete original chunks. Document roles are assigned in this order:

1. explicit `addendum_number` or normalized `document_type`
2. explicit addendum, amendment, clarification/Q&A, solicitation, or RFP language in the first three persisted chunks
3. `other` when neither metadata nor content establishes a role

Filenames and filesystem modification times are never classification or ordering evidence. Multiple addenda use unique addendum numbers first, then unique document dates. If neither establishes a reliable order, `ordering_uncertain` is true and each addendum is compared to base documents without assuming that another addendum supersedes it. If no document is explicitly identifiable as the original solicitation, `relationship_uncertain` is true.

Analysis is bounded and synchronous. For each addendum, the configured LLM first identifies at most 20 explicit topics from capped addendum chunks. Those topic queries retrieve related earlier chunks through the existing hybrid retrieval service. A second structured LLM call classifies requirement-level findings as:

- `new_requirement`
- `modified_requirement`
- `removed_requirement`
- `clarification`
- `conflict`
- `ambiguous`
- `no_change`

Omission is never treated as deletion. `removed_requirement` requires explicit withdrawal evidence. Every model-provided chunk ID must have been supplied in the correct evidence side and must resolve through PostgreSQL to the requested bid and expected document. Unsupported findings are rejected and the run is marked `partial`.

Revision `8d6a1c4e7b22` adds append-only `addendum_analysis_runs` and links requirement-level `addendum_changes` to the run and source documents. Repeated requests preserve earlier runs and findings.

```bash
alembic upgrade head
curl -s -X POST \
  http://127.0.0.1:8000/api/v1/bids/BID_UUID/analyze-addenda
```

Representative response:

```json
{
  "run_id": "RUN_UUID",
  "bid_id": "BID_UUID",
  "status": "completed",
  "relationship_uncertain": false,
  "ordering_uncertain": false,
  "findings": [{
    "id": "FINDING_UUID",
    "analysis_run_id": "RUN_UUID",
    "original_document_id": "ORIGINAL_DOCUMENT_UUID",
    "addendum_document_id": "ADDENDUM_DOCUMENT_UUID",
    "topic": "Submission deadline",
    "classification": "modified_requirement",
    "original_value": "May 1 at 2 PM ET",
    "amended_value": "May 8 at 4 PM ET",
    "explanation": "The addendum explicitly revises the deadline.",
    "original_sources": [{"chunk_id": "ORIGINAL_CHUNK_UUID"}],
    "addendum_sources": [{"chunk_id": "ADDENDUM_CHUNK_UUID"}]
  }]
}
```

The endpoint returns 404 for an unknown bid, 409 for no eligible documents or no explicitly identified addenda, 502 for malformed structured model output, and 503 for provider/retrieval failure. Unit tests mock retrieval and both LLM phases:

```bash
pytest -q tests/test_addendum_analysis_service.py tests/test_addendum_analysis_api.py
pytest -q -m integration tests/test_addendum_analysis_integration.py
```

## Extraction validation and evidence-based confidence

Task 3.3 validates a persisted extraction without changing its values:

```bash
curl -s -X POST \
  http://127.0.0.1:8000/api/v1/bids/BID_UUID/extractions/EXTRACTION_RUN_UUID/validate
```

Validation first resolves every stored citation against PostgreSQL. A citation is accepted only when its chunk, document, bid, filename, page, section, and excerpt agree with the authoritative row. Cross-bid, cross-document, fabricated, stale, and malformed citations are rejected rather than replaced.

The validator then applies exact identifier checks, literal date/time/quantity/unit checks, explicit-negative handling, and bounded semantic verification for interpretive fields such as contract terms, mandatory documents, payment requirements, summaries, and product specifications. Semantic verification receives only the field value and validated evidence, and any returned chunk IDs must belong to that supplied evidence.

The heuristic score is:

```text
0.30 × citation integrity
+ 0.30 × directness of support
+ 0.20 × completeness of support
+ 0.20 × source/value agreement
```

Invalid citations cap confidence at `0.20`; unresolved conflicts cap it at `0.35`. Semantic failure produces `needs_review` below the medium threshold. Missing values have no numeric score. Default categories are high at `>= 0.80`, medium at `>= 0.55`, and low below `0.55`. These scores are transparent heuristics, not calibrated probabilities and not proof of factual correctness.

The latest completed or partial Task 3.2 run is consulted when available. Relevant unresolved conflicts and stale original values are surfaced with citations to both sides. When no addendum run exists, the report explicitly returns `addendum_check_status: "not_run"` and a warning.

Each request creates append-only `validation_runs` and 20 `field_validation_results` rows. The report retains per-status counts instead of hiding conflicts in one average. Overall status is `blocked` for conflicts/unsupported values, `needs_review` for partial, missing, or review-required fields, and `validated` only when every field claim passes.

Revision `c4a91f8d2e60` creates the validation-history tables. Apply it with:

```bash
alembic upgrade head
```

Unit tests mock semantic verification; no live provider call is required:

```bash
pytest -q tests/test_extraction_validation_service.py tests/test_extraction_validation_api.py
pytest -q -m integration tests/test_extraction_validation_integration.py
```

## Final bid intelligence profile

Task 3.4 provides a read-only consolidated view over persisted extraction, addendum-analysis, and validation runs. It never invokes an LLM and never rewrites upstream records:

```bash
curl -s \
  http://127.0.0.1:8000/api/v1/bids/BID_UUID/intelligence-profile
```

The latest completed or partial extraction is selected by default. Select a historical extraction explicitly:

```bash
curl -s \
  "http://127.0.0.1:8000/api/v1/bids/BID_UUID/intelligence-profile?extraction_run_id=EXTRACTION_RUN_UUID"
```

The profile then selects the latest validation for that exact extraction. When validation references an addendum-analysis run, that exact run is used; otherwise the latest compatible addendum run is used. Results from unrelated bids or extraction runs are never mixed.

All 20 assignment fields retain their exact external names. Each field contains the extraction value, validation status, heuristic confidence, validated supporting and contradicting citations, extraction notes, review recommendation, and relevant addendum findings with original and amended values kept separately. Invalid or stale references produce warnings and are omitted rather than replaced.

Profile IDs are deterministic UUID5 values over the bid and selected upstream run IDs. Rebuilding from the same inputs produces the same ID, field values, and statuses; only `generated_at` changes. Profiles are assembled on demand, so Task 3.4 adds no database table or migration.

Status transitions are deterministic:

- `incomplete`: extraction, matching validation, source documents, or required addendum analysis is missing
- `needs_review`: an unsupported, conflicting, or review-required field remains, including critical due-date or contract issues
- `ready_with_warnings`: processing is complete but partial/not-found fields or non-critical warnings remain
- `ready`: all fields are validated with no unresolved issues or warnings
- `failed`: reserved for a failed profile-generation workflow; normal read errors use HTTP error responses

A bid with no addenda does not fail. If explicit addendum metadata exists but no analysis run is available, the profile is `incomplete` and explains why. A missing validation never appears as validated; affected fields use `unvalidated`.

Synthetic response shape:

```json
{
  "metadata": {
    "bid_id": "BID_UUID",
    "bid_identifier": "synthetic-example",
    "profile_id": "PROFILE_UUID",
    "profile_version": 1,
    "extraction_run_id": "EXTRACTION_RUN_UUID",
    "validation_run_id": "VALIDATION_RUN_UUID",
    "addendum_analysis_run_id": "ADDENDUM_RUN_UUID",
    "status": "needs_review",
    "review_required": true
  },
  "fields": {
    "Due Date": {
      "value": "May 1 at 2 PM ET",
      "validation_status": "conflicting",
      "confidence_score": 0.3,
      "supporting_citations": [],
      "contradicting_citations": [],
      "review_recommendation": "Resolve the cited deadline conflict.",
      "addendum_changes": [{
        "classification": "conflict",
        "original_value": "May 1 at 2 PM ET",
        "amended_value": "May 8 at 4 PM ET"
      }]
    }
  }
}
```

The values above are synthetic documentation, not assignment findings.

## Task 3 integrated workflow

`BidIntelligenceWorkflow` is a narrow synchronous coordinator for integration and future internal callers. It invokes the existing services in this order:

```text
structured extraction
→ addendum analysis (skipped only when no addenda exist)
→ validation for that exact extraction run
→ profile for that exact extraction run
```

It does not duplicate extraction, comparison, validation, or profile logic and is not a multi-agent framework. Every completed stage records its persisted run ID. An unexpected addendum or validation failure stops downstream processing and returns a failed stage; it cannot produce a misleading completed profile. A genuine no-addenda result is represented as `skipped` and validation continues.

The profile treats missing or partially supported critical fields—`Due Date`, `Term of Bid`, and `Contract or Cooperative to use`—as `needs_review`, not merely `ready_with_warnings`. Unresolved conflicts likewise propagate from Task 3.2 through validation into the final profile.

The PostgreSQL integration test uses a generated, unseen synthetic bid identifier and persisted original/addendum chunks. Providers are mocked, but all Task 3 stores and history tables are real:

```bash
pytest -q -m integration tests/test_task_3_end_to_end_integration.py
```

The test executes the workflow twice and verifies separate extraction, addendum-analysis, and validation histories, exact 20-field serialization, source-chunk lineage, conflict propagation, and consistent run references. That Task 3 coordinator remains independent of the LangGraph layer below.

## LangGraph bid-intelligence orchestration

Task 4.1 adds `langgraph` as the workflow runtime. `StateGraph` owns the nodes, state transitions, and conditional failure routing:

```text
START
  → validate_bid_context
  → extract_rfp_fields
  → analyze_addenda
  → validate_extraction
  → generate_profile
  → finalize
  → END
```

Every required stage has a conditional edge to `finalize`. Invalid context, missing eligible documents, extraction failure, addendum-analysis failure, validation failure, or profile failure therefore cannot be reported as a successful workflow. A bid with no addenda records `analyze_addenda=skipped`, the reason `no_addenda_present`, and continues to validation. Conflicting or unsupported validation results continue to profile generation with `requires_human_review=true`.

The graph state is defined in `app/agents/state.py`. It contains IDs, stage outcomes, concise summaries, warnings, and sanitized errors—never database sessions, provider clients, prompts, chunks, API keys, or full documents. Graph nodes in `app/agents/workflow.py` call the existing Task 3 services; they do not duplicate retrieval, extraction, addendum comparison, validation, or profile assembly.

Each request creates an append-only `bid_workflow_runs` record. PostgreSQL stores business status and upstream run references and rejects cross-bid run linkage. The endpoint is:

```text
POST /api/v1/bids/{bid_id}/workflow
```

The response includes the workflow ID/version, stage statuses, extraction/addendum/validation run IDs, profile ID/status, summaries, warnings, errors, and review requirement. Calls always create a new workflow history record; there is no implicit reuse of expensive upstream runs.

Checkpoint configuration:

```env
LANGGRAPH_CHECKPOINTING_ENABLED=true
LANGGRAPH_CHECKPOINTER_BACKEND=memory
LANGGRAPH_WORKFLOW_VERSION=1.0
LANGGRAPH_WORKFLOW_TIMEOUT_SECONDS=300
```

The process-wide `InMemorySaver` supports isolated testing and checkpoints within one process. It is **not durable recovery**: checkpoints disappear on restart, no resume endpoint is exposed, and automatic retries are intentionally absent because Task 3 stages create append-only records. PostgreSQL `bid_workflow_runs` remains durable business history. A PostgreSQL LangGraph checkpointer is deferred until its separate driver, table setup, lifecycle, and recovery semantics can be validated; checkpoint tables must not be created during module import.

## Specialized RFP intelligence agents

Task 4.2 extends the same `StateGraph`; it does not create a second orchestrator:

```text
analyze_addenda
  → requirements_agent
  → product_specification_agent
  → commercial_terms_agent
  → compliance_documentation_agent
  → evidence_review_agent
  → validate_extraction
```

The four domain agents run focused hybrid searches scoped to the workflow bid and eligible document IDs. Each sends at most `SPECIALIZED_AGENT_MAX_EVIDENCE_CHUNKS` persisted chunks to the existing LLM provider and requires structured JSON with supplied chunk IDs. Unsupplied IDs are rejected, missing evidence produces a partial result and human-review flag, and malformed or timed-out output fails that required graph stage.

- **Requirements:** scope, obligations, mandatory/optional requirements, exclusions, operational responsibilities, and submission instructions.
- **Product & Specification:** products, exact manufacturer/model/part identifiers, quantities/units, specifications, warranties, accessories, and compatibility.
- **Commercial Terms:** submission and delivery deadlines, triggers, contract terms/renewals, payment, bond/security, warranties, penalties, and cooperative arrangements.
- **Compliance & Documentation:** forms, certifications, eligibility, signatures, registration, submission format/channel, and reference-versus-required documents.
- **Evidence Review:** deterministic PostgreSQL citation validation, missing evidence, cross-bid evidence, duplicates, unresolved questions, and conflicting structured values. Task 3.3 validation still runs afterward and remains authoritative for the 20 extracted fields.

Validated outputs are stored in `specialized_agent_runs` and `specialized_agent_findings`; Task 3 extraction records are not copied. The unique `(workflow_id, agent_name)` and `(agent_run_id, finding_key)` constraints prevent duplicate durable findings. A completed persisted domain-agent result is reused before another provider call, protecting recovery after a database write.

Specialized findings remain bounded in checkpoint state. The workflow API exposes agent statuses, category counts, unresolved conflicts, and the combined human-review decision rather than returning every finding. The Task 3.4 service still generates the final 20-field profile; specialized agents do not silently rewrite extracted or validated field values.

Configuration:

```env
SPECIALIZED_AGENTS_ENABLED=true
SPECIALIZED_AGENT_TIMEOUT_SECONDS=45
SPECIALIZED_AGENT_MAX_EVIDENCE_CHUNKS=12
SPECIALIZED_AGENT_MAX_OUTPUT_TOKENS=6000
```

## LangGraph bid comparison

Task 4.3 adds a dedicated comparison `StateGraph` without changing the single-bid graph:

```text
START
  → validate_comparison_request
  → load_profiles
  → compare_structured_fields
  → compare_semantic_requirements
  → validate_comparison_evidence
  → finalize_comparison
  → END
```

`POST /api/v1/bids/compare` accepts two to five distinct bid IDs by default, optional dimensions, optional per-bid extraction run IDs, and `allow_incomplete_profiles`. Comparison never invokes extraction, addendum analysis, or validation. Task 3.4 rebuilds each selected profile from its persisted extraction/validation/addendum records, and the comparison history stores those exact run references.

Example:

```json
{
  "bid_ids": [
    "00000000-0000-0000-0000-000000000001",
    "00000000-0000-0000-0000-000000000002"
  ],
  "dimensions": ["scope", "products", "commercial_terms", "compliance", "addenda"],
  "allow_incomplete_profiles": false
}
```

Rows use `equivalent`, `different`, `missing`, `conflicting`, `not_comparable`, or `uncertain`. Missing information is never converted into a difference. Conflicting validation or addendum evidence takes precedence. ISO deadlines are compared deterministically only when timezone context is adequate. Exact identifiers, structured quantities/units, model and part numbers, and compatible list/object values are compared without an LLM.

Only bounded narrative rows are sent to the existing LLM provider. Semantic output can update classification, explanation, and confidence for supplied attributes; it cannot introduce values, bids, or citations. If semantic comparison times out or is malformed, deterministic rows are preserved with an explicit warning.

Every returned citation is rechecked against its PostgreSQL chunk and bid through the existing evidence store. Invalid and cross-bid citations are removed and the row becomes uncertain. The original profile field status and addendum changes remain visible in each per-bid value.

`bid_comparison_runs` stores the immutable structured report. `bid_comparison_profiles` records the exact profile ID and extraction, addendum, and validation run IDs used for each bid. Every request creates a new comparison run; later profile changes do not rewrite historical reports.

Configuration:

```env
BID_COMPARISON_MAX_BIDS=5
BID_COMPARISON_SEMANTIC_TIMEOUT_SECONDS=30
BID_COMPARISON_MAX_SEMANTIC_ROWS=20
BID_COMPARISON_MAX_OUTPUT_TOKENS=4000
```

## Evaluation and workflow observability

Task 4.4 provides a reusable `app.evaluation` package:

- `dataset.py` loads versioned Pydantic-validated cases.
- `metrics.py` implements exact match, field accuracy, set precision/recall/F1, missing-value accuracy, completeness, timezone-aware datetime correctness, citation/evidence quality, classification accuracy, and workflow reliability.
- `runner.py` reuses `app.retrieval.evaluation.evaluate` for Recall@K, Precision@K, MRR, Hit Rate@K, and nDCG@K.
- `gates.py` reports `unavailable` rather than passing a threshold when a metric lacks ground truth.
- `reports.py` renders a concise summary while the runner optionally writes the complete JSON report.

The bundled `rfp-intelligence-synthetic-regression` dataset is version `1.0.0`. Every case is explicitly labeled `synthetic_source_authored`; it is not model-generated ground truth and must not be presented as Bid1/Bid2 or real-world accuracy. It covers identifiers, timezone deadlines, product model/part identifiers, missing payment terms, valid-but-unsupported evidence, cross-bid citation rejection, addendum labels, specialized findings, comparison labels, retrieval ranking, and workflow reliability.

Run the deterministic mock evaluation:

```bash
python -m app.evaluation.runner \
  --mode mock \
  --output evaluation-results/synthetic_v1_report.json
```

`--mode live` is deliberately rejected until an explicit live-service adapter and independently labeled real-document dataset are configured. Unit tests, PostgreSQL integration tests, synthetic ground-truth evaluation, and live LLM evaluation remain separate report categories.

Verified invariant gates are configurable:

```env
EVALUATION_EXACT_IDENTIFIER_MIN=1.0
EVALUATION_INVALID_CITATION_REJECTION_MIN=1.0
EVALUATION_CONFLICT_RECALL_MIN=1.0
```

Both LangGraph builders wrap registered nodes with the same failure-isolated instrumentation helper. `workflow_node_events` records operation/workflow ID, bid ID when applicable, node, start/end timestamps, duration, status, retry count, standardized error category, upstream run IDs, and human-review state. It never stores prompts, source text, extracted field values, or secrets. Event persistence/logging failures are caught and cannot turn a successful business node into a failed node.

Error categories are `validation_error`, `retrieval_error`, `llm_provider_error`, `invalid_structured_output`, `citation_validation_error`, `database_error`, `checkpoint_error`, `timeout`, and `unknown_error`. Automatic retries remain disabled, so the observed retry count is currently zero.

## Human review and agent guardrails

Task 4.5 inserts `create_review_items → human_review_gate` between evidence validation and profile generation. Deterministic rules route unsupported or low-confidence fields, invalid/cross-bid citations, critical missing fields, unresolved conflicts, ambiguous deadlines, uncertain product identifiers, and inconsistent agent evidence. High confidence never overrides an invalid citation or critical conflict; missing optional fields do not block the graph.

The gate uses LangGraph `interrupt()` and resumes through `Command(resume=...)`. A resume payload is not approval: the node re-checks PostgreSQL and proceeds only after every required item is resolved. The API does not keep a request open while review is pending.

Review states are `pending`, `approved`, `rejected`, `reevaluation_requested`, and `unresolved`. Only `pending` may receive a decision. Decisions are append-only and idempotent; original proposed values and citations remain unchanged, while a reviewer correction is stored separately. Approving one item does not approve a bid. Re-evaluation reruns only the existing validation stage, updates the suspended checkpoint with the new validation run, and reopens the item as pending.

Review endpoints:

- `GET /api/v1/reviews`
- `GET /api/v1/reviews/{review_id}` (includes decision history)
- `POST /api/v1/reviews/{review_id}/decision`
- `GET /api/v1/workflows/{workflow_id}/review-status`

All endpoints fail closed unless `HUMAN_REVIEW_API_TOKEN` is configured. Send `Authorization: Bearer <token>` and `X-Reviewer-ID: <identity>`. This is a shared-token boundary, not full user authentication or RBAC; the supplied reviewer ID identifies the token holder's asserted actor. Integrate an identity provider before multi-tenant production deployment.

```env
HUMAN_REVIEW_ENABLED=true
HUMAN_REVIEW_CONFIDENCE_THRESHOLD=0.55
HUMAN_REVIEW_CRITICAL_FIELDS=Due Date,Term of Bid,Contract or Cooperative to use
HUMAN_REVIEW_MAX_REEVALUATION_ATTEMPTS=2
HUMAN_REVIEW_API_TOKEN=
```

PostgreSQL review history is durable. LangGraph checkpoints are currently stored in `InMemorySaver`; suspended graphs cannot resume after a process restart or from another worker. The decision endpoint checks for the checkpoint and returns HTTP 409 without recording a decision when it is missing. Replace this with a persistent LangGraph checkpointer before horizontally scaled or restart-safe deployment.

## Browser uploads and background processing

Bids can be created, populated, and processed entirely over HTTP. Apply the migrations first (`alembic upgrade head`). Revision `d81e3b5f6a20` adds the `processing_jobs` table and the `documents.document_role`, `original_filename`, and `uploaded_at` columns.

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/uploads/limits` | Upload size, count, extension, and role limits the server enforces |
| `POST /api/v1/bids` | Create a bid (`bid_identifier` required; `title`, `solicitation_number`, `issuing_organization` optional). HTTP 409 returns `{message, bid_id}` when the identifier exists |
| `GET /api/v1/bids?limit=&offset=&search=` | Paginated bid list with document counts, latest job, and latest workflow status |
| `GET /api/v1/bids/{bid_id}` | Bid detail with documents and job history |
| `GET /api/v1/bids/{bid_id}/documents` | Registered documents for one bid |
| `POST /api/v1/bids/{bid_id}/documents` | Multipart upload: repeated `files`, plus optional `roles` and `addendum_numbers` aligned by index |
| `POST /api/v1/bids/{bid_id}/process` | Queue `ingest_and_analyze` (default) or `analyze_only`; HTTP 202 returns the job |
| `GET /api/v1/bids/{bid_id}/jobs`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{job_id}` | Job status, stage, attempts, errors, and result summary |
| `GET /api/v1/documents/{document_id}/file` | Stream the stored file. PDFs are inline; HTML is always an attachment. `?download=true` forces an attachment |

Uploads are streamed to a hidden temporary file in `INGESTION_INPUT_ROOT/<bid_identifier>/` while the server enforces `UPLOAD_MAX_FILE_BYTES` and computes SHA-256. The content must match the extension (`%PDF` for PDF; HTML markup for HTML). The file is then atomically renamed to `<random>_<sanitized name>` and registered through the existing document registry. Identical content already stored for the same bid is reported as `duplicate` without writing a second copy; the same file in a different bid is registered separately. Each file reports `registered`, `duplicate`, or `rejected` with a reason, so one bad file does not fail the batch. Uploads are refused with HTTP 409 while the bid has an active job.

Roles are `auto`, `original`, `addendum`, `clarification`, and `attachment`. A declared role is stored in `document_role` and takes precedence over content-based addendum detection; `document_type` remains the file format used by the parser.

Processing runs on a PostgreSQL-backed queue. Workers claim jobs with `SELECT … FOR UPDATE SKIP LOCKED`, a partial unique index allows only one queued or running job per bid, and running jobs send heartbeats. A job whose heartbeat is older than `JOB_STALE_AFTER_SECONDS` is requeued as `worker_lost`. Retryable failures (all documents failed to ingest, workflow timeout, or runtime error) are retried with exponential backoff up to `JOB_MAX_ATTEMPTS`. A bid with no documents fails immediately. Workflows that finish as `completed`, `completed_with_warnings`, or `needs_review` count as success.

```env
UPLOAD_MAX_FILE_BYTES=52428800
UPLOAD_MAX_FILES_PER_REQUEST=20
UPLOAD_ALLOWED_EXTENSIONS=.pdf,.html,.htm
JOB_WORKER_EMBEDDED=true
JOB_POLL_INTERVAL_SECONDS=2
JOB_MAX_ATTEMPTS=3
JOB_STALE_AFTER_SECONDS=900
JOB_HEARTBEAT_INTERVAL_SECONDS=30
CORS_ALLOWED_ORIGINS=
```

Worker modes:

- **Embedded (default).** `JOB_WORKER_EMBEDDED=true` starts the worker inside the API process, so `uvicorn app.main:app` is all you need. On shutdown the worker stops claiming jobs and releases an in-flight job back to the queue.
- **Standalone.** Set `JOB_WORKER_EMBEDDED=false` on the API and run `python -m app.worker` separately. Do this only after you replace the in-memory LangGraph checkpointer. With `InMemorySaver`, a workflow paused for human review can only be resumed by the process that ran it, and review decisions are handled by the API process.

Production notes:

- Configure your reverse proxy's request body limit (for example nginx `client_max_body_size`) to at least `UPLOAD_MAX_FILE_BYTES` times the number of files per request.
- `CORS_ALLOWED_ORIGINS` is empty by default because the Vite dev server and a same-origin deployment need no CORS. Set a comma-separated list of origins only when the frontend is served from another origin.
- These endpoints have no user authentication or per-bid authorization. Deploy them behind an authenticated gateway or private network.
- Paused reviews cannot resume after a restart until a persistent checkpointer replaces `InMemorySaver` (see Human review above).
- Uploaded files live on the local disk under `INGESTION_INPUT_ROOT`. Use shared or persistent storage when running more than one API instance.

Example:

```bash
BID=$(curl -s -X POST http://127.0.0.1:8000/api/v1/bids \
  -H 'Content-Type: application/json' -d '{"bid_identifier":"Docks-2026"}' | python -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -s -F files=@rfp.pdf -F roles=original "http://127.0.0.1:8000/api/v1/bids/$BID/documents"
JOB=$(curl -s -X POST "http://127.0.0.1:8000/api/v1/bids/$BID/process" | python -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -s "http://127.0.0.1:8000/api/v1/jobs/$JOB"
```

## Ask questions about a bid

`POST /api/v1/bids/{bid_id}/ask` answers a free-form question using only that bid's indexed documents and stored analysis results. The chat is read-only: it never creates, approves, rejects, re-evaluates, or re-runs anything.

```json
{
  "question": "And what bond is required?",
  "history": [
    {"role": "user", "content": "What insurance is required?"},
    {"role": "assistant", "content": "General liability of $1,000,000 [S1]."}
  ],
  "document_ids": [],
  "compare_bid_ids": []
}
```

How an answer is built:

1. **Search.** The service runs the bid-scoped hybrid search (`QA_TOP_K` passages). For follow-up questions, the previous user question is added to the search text so short questions like "and the bond?" still find the right passages. The search text is also expanded with the wording procurement documents use, for example "solicitation due" and "closing date" for a deadline question. Results from any other bid are discarded.
2. **Stored records.** The bid's stored profile fields (value, validation status, confidence, contradicting quotes, addendum changes), the latest addendum findings (up to `QA_MAX_ADDENDUM_RECORDS`) and its pending review items (up to `QA_MAX_REVIEW_RECORDS`) are added as records `R1..Rn`.
3. **Policy notes.** For questions about reviews, validation, agents, or the workflow, fixed notes describing how the application works are added as `P1..Pn`. They are written in code and checked against the review and validation services.
4. **Model call.** When there are no passages, records or notes, the response is `found: false` and the LLM is not called. Otherwise passages are numbered `S1..Sn` (each truncated to `QA_MAX_PASSAGE_CHARACTERS`) and sent to `LLM_MODEL` with the last `QA_MAX_HISTORY_TURNS` turns.
   - Citation ids are removed from earlier answers so they cannot be reused.
   - The model must cite every fact and list every value when sources disagree.
   - It must not move facts between bids or claim to take actions.
5. **Repeat guard.** If the answer repeats the previous answer to a different question, the model is asked once more; a second repeat returns "I could not find a supported answer…".
6. **Citation check.** Citations are checked against this turn's evidence. Unknown ids and their markers are removed. An answer that claims `found: true` but has no valid citation is replaced with "I could not find a supported answer…" and `found: false`.

**Comparing bids.** `compare_bid_ids` (at most `QA_MAX_COMPARE_BIDS`, default 1) adds other bids to the conversation. Each bid is searched separately, and `document_ids` applies only to the primary bid. Every passage and record is labelled with its bid.

**All-fields requests.** A request for "all 20 fields" (or five or more field names) is answered from the stored intelligence profile without calling the model. The answer is JSON with the 20 fields: each has its value, validation status, confidence score and citations. The response carries the warning "Answered from the stored bid profile; no new extraction was run." When no profile exists, the chat says so instead of extracting. Questions that ask the chat to act ("Approve…", "Create a review item…") get a read-only warning.

The response contains `answer`, `found`, `sources`, `retrieval_mode`, `warnings`, and `model`. Each source has these fields:
- `source_id`
- `kind`: `passage`, `record` or `policy`
- `bid_id` and `bid_label`
- `document_id`, `chunk_id`, `file_name`, `page_number` and `section_title`
- `excerpt`
- `record`: the record text

A record source appears once for each stored citation. A record without citations has no document fields.

Errors:
- 404: unknown bid, or unknown comparison bid.
- 409 `bid_not_indexed`: the bid or a comparison bid has no indexed documents yet.
- 422 `invalid_compare_bids`: a bid compared with itself, or too many comparison bids.
- 503 `retrieval_unavailable` or `llm_not_configured`.
- 502 `answer_generation_failed`: the model call failed or returned malformed output.

The conversation is not stored; the client sends recent turns with each question. Each question costs one query embedding and one LLM call per bid in scope, plus one retry when the repeat guard fires. Query-rewrite calls are added only when `QUERY_REWRITING_ENABLED` is on.

```env
QA_TOP_K=10
QA_MAX_HISTORY_TURNS=6
QA_MAX_OUTPUT_TOKENS=1200
QA_MAX_PASSAGE_CHARACTERS=1500
QA_MAX_COMPARE_BIDS=1
QA_MAX_ADDENDUM_RECORDS=15
QA_MAX_REVIEW_RECORDS=15
```

To re-run the evaluation question set against two processed bids (the chat is read-only, so this changes nothing):

```bash
python -m scripts.chat_eval PRIMARY_BID_ID SECONDARY_BID_ID --output chat_eval_results.json
```

## Run tests

From the `backend` directory, with the virtual environment activated.

The test configuration sets `JOB_WORKER_EMBEDDED=false`, so tests never start a background worker. `tests/test_job_queue_integration.py` (marked `integration`) exercises job claiming, retries, and stale requeue against the real PostgreSQL database. `test_specialized_agent_integration` counts rows across the whole database, so it fails when the local database already holds bids from manual use; run it against an empty database.

Unit tests check health and readiness with mocked dependencies, SQLAlchemy metadata, the Qdrant service, logging, request ids, the LLM and embedding services, document registration, PDF and HTML parsing, chunking, indexing, and hybrid retrieval. Registration, parser, and chunking tests use temporary files or an in-memory chunk store. Indexing and retrieval tests use fakes for providers and Qdrant. Provider tests use a mocked OpenAI client. They do not need PostgreSQL, Qdrant, or an API key:

```bash
pytest -q -m "not integration and not qdrant"
```

The PostgreSQL integration tests connect to `DATABASE_URL` and roll their transactions back. One runs `SELECT 1`. The registration test inserts a bid and document, checks duplicate and changed-file behavior, and rolls that work back. If a registration row is committed, the test deletes only the `test-ingest-` bid from that run. These tests do not use SQLite. If PostgreSQL cannot be reached, or the database does not exist yet, the test is skipped:

```bash
pytest -q -m integration
```

The Qdrant integration test needs a server at `QDRANT_URL`. It creates a temporary collection, upserts two vectors, checks a bid filter, and deletes that temporary collection. It does not use an in-memory client. If Qdrant cannot be reached, the test is skipped:

```bash
pytest -q -m qdrant
```

Run every test:

```bash
pytest -q
```

These commands work on macOS, Linux, and Windows.
