# Architecture

The diagrams below are drawn from the code. Each section names the files it describes.

## 1. System overview

The React UI and the CLI call the same backend services. PostgreSQL is the source of truth for documents, chunks, every run and its findings, review items and job history. Qdrant holds only the chunk embeddings and the payload needed to filter and cite them. OpenAI provides embeddings and the chat model.

```mermaid
flowchart LR
    UI[React UI - frontend/src] -->|/api via Vite proxy| API[FastAPI - app/main.py]
    CLI[CLI - app/cli.py] --> Services
    API --> Services[Services and LangGraph graphs]
    API -->|POST /bids/id/process| Queue[(processing_jobs)]
    Worker[Job worker - app/jobs] -->|claims job| Queue
    Worker --> Services
    Services --> PG[(PostgreSQL)]
    Services --> QD[(Qdrant)]
    Services --> OAI[OpenAI embeddings and LLM]
    Files[Bid folders and uploads] --> Services
```

- The worker runs inside the API process by default (`JOB_WORKER_EMBEDDED=true`); `python -m app.worker` runs it on its own.
- Jobs are claimed with `SELECT … FOR UPDATE SKIP LOCKED`, and each job sends heartbeats while it runs.

## 2. Ingestion and the search engine

Code: `app/ingestion/` (registry, parsers, chunking, indexing, `end_to_end_pipeline.py`) and `app/retrieval/` (`semantic_search.py`, `keyword_search.py`, `fusion.py`, `query_rewriter.py`, `reranker.py`, `retrieval_service.py`, `citations.py`).

```mermaid
flowchart TB
    subgraph Ingestion
        Scan[Register files - SHA-256 dedupe] --> Parse[Parse - PyMuPDF for PDF, BeautifulSoup for HTML]
        Parse --> Chunk[Chunk - text 1000 chars with 150 overlap, tables up to 20 rows]
        Chunk --> Store[(document_chunks in PostgreSQL)]
        Store --> Embed[Embed - text-embedding-3-small, 1536 dimensions]
        Embed --> Index[(Qdrant points keyed by chunk id)]
    end
    subgraph Search[Hybrid search - RetrievalService]
        Query[Question plus bid filter] --> Rewrite[Optional LLM rewrite - original query kept first]
        Rewrite --> Sem[Semantic - Qdrant cosine]
        Rewrite --> Key[Keyword - PostgreSQL full text and identifier match]
        Sem --> RRF[Reciprocal Rank Fusion k=60]
        Key --> RRF
        RRF --> Cite[Citation check - every chunk id resolved in PostgreSQL]
        Cite --> Rerank[Optional cross-encoder rerank]
        Rerank --> Results[Ranked chunks with file, page, section]
    end
    Index -.-> Sem
    Store -.-> Key
    Store -.-> Cite
```

- If one search backend fails and `SEARCH_ALLOW_DEGRADED=true`, the search returns `semantic_only` or `keyword_only` results with a warning.
- Extraction turns query rewriting and reranking off. The chat uses the settings in `.env`.

## 3. Agents and how they communicate

Code: `app/agents/workflow.py` (graph), `app/agents/state.py` (state), `app/agents/specialized.py` (agent definitions), `app/observability/instrumentation.py` (node events).

The agents do not call each other or exchange chat messages. They communicate through two channels:

1. **The LangGraph state** (`BidIntelligenceState`), which carries only ids, stage statuses, short summaries, warnings and errors. It never carries document text or prompts.
2. **PostgreSQL run records.** Each node writes its own run and findings, then puts the run id into the state. A later node loads what it needs by that id. For example, validation reads the extraction run and the latest addendum run, and the profile is built from the extraction, validation and addendum run ids.

```mermaid
flowchart TB
    Start([START]) --> V[validate_bid_context]
    V --> E[extract_rfp_fields - LLM]
    E --> A[analyze_addenda - LLM, 2 phases]
    A --> R[requirements_agent - LLM]
    R --> P[product_specification_agent - LLM]
    P --> C[commercial_terms_agent - LLM]
    C --> D[compliance_documentation_agent - LLM]
    D --> ER[evidence_review_agent - deterministic]
    ER --> VX[validate_extraction - rules plus LLM check]
    VX --> RI[create_review_items - rules]
    RI --> HG[human_review_gate - LangGraph interrupt]
    HG --> G[generate_profile - no LLM]
    G --> F[finalize]
    F --> End([END])

    E -.->|extraction_runs, extracted_fields| DB[(PostgreSQL)]
    A -.->|addendum_analysis_runs, addendum_changes| DB
    R -.->|specialized_agent_runs, findings| DB
    P -.-> DB
    C -.-> DB
    D -.-> DB
    ER -.-> DB
    VX -.->|validation_runs, field_validation_results| DB
    RI -.->|review items| DB
    F -.->|bid_workflow_runs| DB
    E -->|hybrid search| S[RetrievalService]
    A --> S
    R --> S
    P --> S
    C --> S
    D --> S
```

- **Failure routing:** every node except `finalize` has a conditional edge (`route_after_stage`) to `finalize`. A failed stage therefore ends the run with a failed status and can never produce a profile reported as successful. These failure edges are left out of the diagram for readability.
- **Node events:** every node is wrapped by `instrument_node`, which writes a `workflow_node_events` row with the node name, timing, status, error category and the run ids at that point. The agent trace in `agent_trace/` is rebuilt from these rows and the run tables.
- **Human review:** `create_review_items` applies deterministic rules, such as low confidence, invalid citations or unresolved conflicts, to open review items. When `HUMAN_REVIEW_ENABLED=true`, `human_review_gate` pauses the graph with `interrupt()`, and the reviewer's decisions resume it with `Command(resume=…)`. With review disabled, as in the local `.env`, the gate is skipped and the profile is marked `needs_review`.
- **Checkpoints:** the graph uses `InMemorySaver`. Checkpoints are lost on restart, and PostgreSQL keeps the durable history.

### Bid comparison graph

Code: `app/agents/bid_comparison_agent.py`.

```mermaid
flowchart LR
    S2([START]) --> VR[validate_comparison_request]
    VR --> LP[load_profiles]
    LP --> SF[compare_structured_fields - deterministic]
    SF --> SR[compare_semantic_requirements - LLM, bounded rows]
    SR --> VE[validate_comparison_evidence]
    VE --> FC[finalize_comparison]
    FC --> E2([END])
    LP -.->|rebuilds profiles from stored runs| DB2[(PostgreSQL)]
    FC -.->|bid_comparison_runs, bid_comparison_profiles| DB2
```

Comparison never re-runs extraction. It rebuilds each bid's profile from the stored runs and records which run ids it used.

## 4. Bid chat (question answering)

Code: `app/services/bid_qa_service.py` and `app/services/bid_qa_context.py`. The chat is read-only.

```mermaid
flowchart TB
    Q[Question plus recent turns] --> AF{Asks for all 20 fields?}
    AF -->|yes| Prof[Answer from stored profile - no LLM call]
    AF -->|no| X[Expand query - previous question and procurement synonyms]
    X --> HS[Hybrid search scoped to the bid]
    HS --> Ctx[Passages S1..Sn, stored records R1..Rn, policy notes P1..Pn]
    Ctx --> LLM[LLM answer - must cite every fact]
    LLM --> RG[Repeat guard - one retry]
    RG --> CC[Citation check - unknown ids removed]
    CC --> Ans[Answer, sources, warnings]
```

When comparison bids are given, each bid is searched on its own, and every passage and record is labelled with its bid.

## Where the prompts are

Every prompt that receives document text starts with `UNTRUSTED_DOCUMENT_POLICY` from `app/core/prompt_security.py`, which tells the model to treat document text as data and not as instructions. The query rewriter sees only the user's question, so it has its own short system prompt without that policy.

| Stage | Prompt |
|---|---|
| Field extraction | `app/services/extraction_service.py` `SYSTEM_PROMPT` |
| Addendum analysis | `app/services/addendum_analysis_service.py` `DISCOVERY_SYSTEM_PROMPT`, `COMPARISON_SYSTEM_PROMPT` |
| Specialized agents | `app/agents/specialized.py` `AGENT_DEFINITIONS` |
| Validation (semantic check) | `app/services/extraction_validation_service.py` `SEMANTIC_SYSTEM_PROMPT` |
| Query rewriting | `app/retrieval/query_rewriter.py` `QueryRewriter.rewrite` |
| Comparison | `app/agents/bid_comparison_agent.py` (semantic comparison node) |
| Chat | `app/services/bid_qa_service.py` `SYSTEM_PROMPT` |
