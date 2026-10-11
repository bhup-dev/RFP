# RFP Intelligence Platform Frontend

React + Vite frontend foundation for the FastAPI RFP intelligence backend. The project uses JavaScript and JSX only.

## Setup

Requirements: Node.js 20 or newer and the backend running on port 8000.

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` and `/health` to `http://127.0.0.1:8000`, so no permissive backend CORS configuration is needed for local development.

Public browser configuration:

```env
VITE_API_BASE_URL=/api
```

Never place API keys or `HUMAN_REVIEW_API_TOKEN` in a `VITE_` variable; Vite exposes those values to browser users.

## Commands

```bash
npm run dev
npm run build
npm run preview
npm run lint
npm run test
```

## Structure

- `src/app`: application routes and root component
- `src/components/layout`: responsive shell and page framing
- `src/components/common`: reusable loading, error, empty, and status states
- `src/pages`: initial product routes
- `src/services`: centralized API client and domain services
- `src/styles`: visual system and responsive styles
- `src/test`: Vitest and Testing Library coverage

## Integrated backend contracts

- `GET /health/ready`
- `GET /api/v1/uploads/limits`
- `POST /api/v1/bids`, `GET /api/v1/bids`, `GET /api/v1/bids/{bid_id}`
- `POST /api/v1/bids/{bid_id}/documents` (multipart upload)
- `POST /api/v1/bids/{bid_id}/process`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/documents/{document_id}/file`
- `POST /api/v1/bids/{bid_id}/ask`
- `GET /api/v1/bids/{bid_id}/intelligence-profile`
- `POST /api/v1/bids/compare`
- `POST /api/v1/ingestion/scan`
- `POST /api/v1/ingestion/process`
- `POST /api/v1/ingestion/documents/{document_id}/process`

**Upload flow.** `New bid` (`/bids/new`) creates a bid, then uploads each file separately with an XHR progress bar. Each file has a role (detect from content, original, addendum with number, clarification, or attachment). Files with the wrong type, no content, or too large a size are flagged before upload using the limits from `GET /uploads/limits`, and the server re-validates every file. Per-file outcomes (`registered`, `duplicate`, `rejected` with reason) are shown as returned. `Start processing` queues a background job. The job panel polls `GET /jobs/{id}` with backoff and shows the stage (queued, parsing and indexing, analyzing, done), scheduled retries, failures with a retry button, and the result summary. On success it links to the intelligence profile. Use `/bids/new?bid=<id>` to add files to an existing bid.

The bid list (`/bids`) is loaded from the backend with search and pagination. The bid page shows stored documents with open and download links, processing history, and a `Process & analyze` action; a bid without a profile shows "Not processed yet" instead of an error. Citations link to the source document, at the cited page for PDFs. The dashboard lists recent jobs, and the comparison page can pick stored bids.

**Ask about this bid.** The bid page has a chat panel (also reachable from the `Ask questions` header button) backed by `POST /api/v1/bids/{bid_id}/ask`. Answers come only from passages retrieved from that bid's indexed documents, its stored analysis results (profile fields, addendum findings, open review items), and, for workflow questions, fixed notes on how the application works. Inline `[S1]`, `[R1]` and `[P1]` chips jump to the cited source. Each source shows file, page, section and excerpt, with an `Open at page N` link when it points to a stored document. Record and policy sources are labelled "Bid record" and "System policy". The chat is read-only. A `Compare with` select adds one other indexed bid. When two bids are in scope, every source shows its bid, and changing the compared bid starts a new conversation. Answers the documents do not support are labelled "Not found in the documents". Follow-up questions send the last six turns as context. Enter sends and Shift+Enter adds a new line; a request can be stopped, a failed question retried, and the chat cleared. The conversation lives only in the page and is lost on refresh. The panel is disabled until the bid has indexed documents.

Server folder ingestion is still available under `Advanced: server folder ingestion` on the bids page. It scans a directory relative to the backend's `INGESTION_INPUT_ROOT` and runs ingestion synchronously.

The bid profile route renders the exact 20 assignment fields from the intelligence profile response. It supports field-name search, validation-status filtering, review-only filtering, expandable supporting and contradicting citations, profile lineage, and field-level addendum changes. Citation excerpts are shown with their original document and chunk identifiers.

The comparison route verifies manually entered bid IDs through their intelligence-profile endpoint, then submits selected profile IDs and dimensions to `POST /api/v1/bids/compare`. It renders the synchronous report summary, all six comparison classifications, side-by-side values, bid-scoped evidence, filters, and profile links. Comparison history and export are not offered because the backend has no read or export endpoints.

The human-review routes integrate `GET /api/v1/reviews`, `GET /api/v1/reviews/{review_id}`, `GET /api/v1/workflows/{workflow_id}/review-status`, and `POST /api/v1/reviews/{review_id}/decision`. Reviewer credentials must be entered at runtime and remain only in application memory. The UI supports the backend decisions `approve`, `reject`, `request_re_evaluation`, and `mark_unresolved`, including confirmation, required comments, corrected JSON-compatible values, idempotency keys, decision history, and exact workflow-resume feedback.

The backend's current review authentication is a shared bearer token plus a caller-supplied `X-Reviewer-ID`. It has no per-user identity verification or role authorization. The frontend reports this limitation and does not treat hidden or disabled controls as an authorization boundary.

The review service models the existing protected review endpoints, but the page does not call them because the backend currently relies on a shared bearer token that must not be embedded in browser code.

## Known backend gaps

The backend does not currently expose:

- user authentication or per-bid authorization for the bid, upload, and job endpoints
- browser-safe reviewer authentication
- workflow and comparison history list or detail endpoints
- extraction, validation, and addendum-analysis history GET endpoints
- comparison-result retrieval and comparison export endpoints
- document deletion or replacement

The UI does not offer these capabilities and does not generate placeholder production data. Reviews paused in the backend's in-memory LangGraph checkpointer cannot be resumed after a backend restart.
