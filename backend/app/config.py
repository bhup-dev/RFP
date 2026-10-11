"""Application settings loaded from the environment and an optional .env file."""

from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
# Must match app.ingestion.file_utils.SUPPORTED_EXTENSIONS (importing it here is circular).
_PARSER_EXTENSIONS = frozenset({".pdf", ".html", ".htm"})
DEFAULT_DEVELOPMENT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/rfp_intelligence"
)


class Settings(BaseSettings):
    """Runtime settings. Defaults are enough to start the API with no secrets."""

    model_config = SettingsConfigDict(
        env_file=_BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "RFP Intelligence Platform"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/api"
    log_level: str = "INFO"
    log_format: str = "auto"
    request_timeout_seconds: float = 60.0
    health_check_timeout_seconds: float = 2.0
    storage_path: Path = Field(default=_BACKEND_ROOT / "storage")
    ingestion_input_root: Path = Field(default=_BACKEND_ROOT / "data" / "bids")
    database_url: str = Field(
        default=DEFAULT_DEVELOPMENT_DATABASE_URL,
        repr=False,
    )
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = Field(default=None, repr=False)
    qdrant_collection_name: str = "rfp_document_chunks"
    llm_provider: str = "openai"
    llm_model: str = "gpt-5.5"
    openai_api_key: str | None = Field(default=None, repr=False)
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    embedding_batch_size: int = 32
    search_rrf_constant: int = 60
    search_allow_degraded: bool = True
    query_rewriting_enabled: bool = True
    query_rewrite_max_variants: int = 3
    query_rewrite_timeout_seconds: float = 10.0
    reranking_enabled: bool = False
    reranker_provider: str = "sentence_transformers"
    reranker_model: str = "BAAI/bge-reranker-base"
    rerank_candidate_limit: int = 30
    rerank_batch_size: int = 8
    rerank_timeout_seconds: float = 15.0
    rerank_max_input_characters: int = 8000
    extraction_query_top_k: int = 12
    extraction_max_evidence_chunks: int = 60
    extraction_max_output_tokens: int = 8000
    addendum_max_chunks_per_document: int = 30
    addendum_related_top_k: int = 8
    addendum_max_evidence_chunks: int = 60
    addendum_max_output_tokens: int = 8000
    validation_semantic_enabled: bool = True
    validation_high_confidence_threshold: float = 0.80
    validation_medium_confidence_threshold: float = 0.55
    validation_max_output_tokens: int = 2000
    langgraph_checkpointing_enabled: bool = True
    langgraph_checkpointer_backend: str = "memory"
    langgraph_workflow_version: str = "1.0"
    langgraph_workflow_timeout_seconds: float = 300.0
    specialized_agents_enabled: bool = True
    specialized_agent_timeout_seconds: float = 45.0
    specialized_agent_max_evidence_chunks: int = 12
    specialized_agent_max_output_tokens: int = 6000
    bid_comparison_max_bids: int = 5
    bid_comparison_semantic_timeout_seconds: float = 30.0
    bid_comparison_max_semantic_rows: int = 20
    bid_comparison_max_output_tokens: int = 4000
    evaluation_exact_identifier_min: float = 1.0
    evaluation_invalid_citation_rejection_min: float = 1.0
    evaluation_conflict_recall_min: float = 1.0
    qa_top_k: int = 10
    qa_max_history_turns: int = 6
    qa_max_output_tokens: int = 1200
    qa_max_passage_characters: int = 1500
    qa_max_compare_bids: int = 1
    qa_max_addendum_records: int = 15
    qa_max_review_records: int = 15
    human_review_enabled: bool = True
    human_review_confidence_threshold: float = 0.55
    human_review_critical_fields: str = (
        "Due Date,Term of Bid,Contract or Cooperative to use"
    )
    human_review_max_reevaluation_attempts: int = 2
    human_review_api_token: str | None = Field(default=None, repr=False)
    chunk_size: int = 1000
    chunk_overlap: int = 150
    table_chunk_max_rows: int = 20
    chunk_min_text_length: int = 50
    upload_max_file_bytes: int = 50 * 1024 * 1024
    upload_max_files_per_request: int = 20
    upload_allowed_extensions: str = ".pdf,.html,.htm"
    job_worker_embedded: bool = True
    job_poll_interval_seconds: float = 2.0
    job_max_attempts: int = 3
    job_stale_after_seconds: float = 900.0
    job_heartbeat_interval_seconds: float = 30.0
    cors_allowed_origins: str = ""

    @field_validator("database_url")
    @classmethod
    def database_url_uses_async_postgres(cls, value: str) -> str:
        """Reject non-PostgreSQL URLs. SQLite is not a supported fallback."""
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must start with postgresql+asyncpg://")
        return value

    @field_validator("qdrant_api_key", "openai_api_key", mode="before")
    @classmethod
    def blank_api_key_is_unset(cls, value: object) -> object:
        """Treat a blank API key as unset so import and startup do not require one."""
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "app_name",
        "environment",
        "qdrant_url",
        "qdrant_collection_name",
        "llm_provider",
        "llm_model",
        "embedding_provider",
        "embedding_model",
        "reranker_provider",
        "reranker_model",
        "langgraph_checkpointer_backend",
        "langgraph_workflow_version",
    )
    @classmethod
    def text_setting_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @field_validator("log_level")
    @classmethod
    def log_level_is_known(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _LOG_LEVELS:
            allowed = ", ".join(sorted(_LOG_LEVELS))
            raise ValueError(f"LOG_LEVEL must be one of: {allowed}")
        return normalized

    @field_validator("embedding_dimension")
    @classmethod
    def embedding_dimension_is_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("EMBEDDING_DIMENSION must be a positive integer")
        return value

    @field_validator("embedding_batch_size")
    @classmethod
    def embedding_batch_size_is_bounded(cls, value: int) -> int:
        """Bound each embedding request and each database page."""
        if value < 1 or value > 256:
            raise ValueError("EMBEDDING_BATCH_SIZE must be from 1 to 256")
        return value

    @field_validator("search_rrf_constant")
    @classmethod
    def search_rrf_constant_is_positive(cls, value: int) -> int:
        if value < 1 or value > 1000:
            raise ValueError("SEARCH_RRF_CONSTANT must be from 1 to 1000")
        return value

    @field_validator("query_rewrite_max_variants")
    @classmethod
    def query_rewrite_variants_are_bounded(cls, value: int) -> int:
        if value < 1 or value > 5:
            raise ValueError("QUERY_REWRITE_MAX_VARIANTS must be from 1 to 5")
        return value

    @field_validator("rerank_candidate_limit")
    @classmethod
    def rerank_candidate_limit_is_bounded(cls, value: int) -> int:
        if value < 1 or value > 200:
            raise ValueError("RERANK_CANDIDATE_LIMIT must be from 1 to 200")
        return value

    @field_validator("rerank_batch_size")
    @classmethod
    def rerank_batch_size_is_bounded(cls, value: int) -> int:
        if value < 1 or value > 64:
            raise ValueError("RERANK_BATCH_SIZE must be from 1 to 64")
        return value

    @field_validator("rerank_max_input_characters")
    @classmethod
    def rerank_input_is_bounded(cls, value: int) -> int:
        if value < 256 or value > 100_000:
            raise ValueError("RERANK_MAX_INPUT_CHARACTERS must be from 256 to 100000")
        return value

    @field_validator("extraction_query_top_k")
    @classmethod
    def extraction_query_top_k_is_bounded(cls, value: int) -> int:
        if value < 1 or value > 50:
            raise ValueError("EXTRACTION_QUERY_TOP_K must be from 1 to 50")
        return value

    @field_validator("extraction_max_evidence_chunks")
    @classmethod
    def extraction_evidence_is_bounded(cls, value: int) -> int:
        if value < 20 or value > 200:
            raise ValueError("EXTRACTION_MAX_EVIDENCE_CHUNKS must be from 20 to 200")
        return value

    @field_validator("extraction_max_output_tokens")
    @classmethod
    def extraction_output_is_bounded(cls, value: int) -> int:
        if value < 1000 or value > 32000:
            raise ValueError("EXTRACTION_MAX_OUTPUT_TOKENS must be from 1000 to 32000")
        return value

    @field_validator("addendum_max_chunks_per_document")
    @classmethod
    def addendum_document_chunks_are_bounded(cls, value: int) -> int:
        if value < 1 or value > 100:
            raise ValueError("ADDENDUM_MAX_CHUNKS_PER_DOCUMENT must be from 1 to 100")
        return value

    @field_validator("addendum_related_top_k")
    @classmethod
    def addendum_related_top_k_is_bounded(cls, value: int) -> int:
        if value < 1 or value > 50:
            raise ValueError("ADDENDUM_RELATED_TOP_K must be from 1 to 50")
        return value

    @field_validator("addendum_max_evidence_chunks")
    @classmethod
    def addendum_evidence_is_bounded(cls, value: int) -> int:
        if value < 10 or value > 200:
            raise ValueError("ADDENDUM_MAX_EVIDENCE_CHUNKS must be from 10 to 200")
        return value

    @field_validator("addendum_max_output_tokens")
    @classmethod
    def addendum_output_is_bounded(cls, value: int) -> int:
        if value < 1000 or value > 32000:
            raise ValueError("ADDENDUM_MAX_OUTPUT_TOKENS must be from 1000 to 32000")
        return value

    @field_validator(
        "validation_high_confidence_threshold",
        "validation_medium_confidence_threshold",
        "evaluation_exact_identifier_min",
        "evaluation_invalid_citation_rejection_min",
        "evaluation_conflict_recall_min",
        "human_review_confidence_threshold",
    )
    @classmethod
    def score_threshold_is_unit_interval(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("score thresholds must be from 0 to 1")
        return value

    @field_validator("validation_max_output_tokens")
    @classmethod
    def validation_output_is_bounded(cls, value: int) -> int:
        if value < 500 or value > 8000:
            raise ValueError("VALIDATION_MAX_OUTPUT_TOKENS must be from 500 to 8000")
        return value

    @field_validator(
        "request_timeout_seconds",
        "health_check_timeout_seconds",
        "query_rewrite_timeout_seconds",
        "rerank_timeout_seconds",
        "langgraph_workflow_timeout_seconds",
        "specialized_agent_timeout_seconds",
        "bid_comparison_semantic_timeout_seconds",
    )
    @classmethod
    def timeout_is_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout must be greater than 0")
        return value

    @field_validator("specialized_agent_max_evidence_chunks")
    @classmethod
    def specialized_evidence_is_bounded(cls, value: int) -> int:
        if value < 1 or value > 50:
            raise ValueError("SPECIALIZED_AGENT_MAX_EVIDENCE_CHUNKS must be from 1 to 50")
        return value

    @field_validator("specialized_agent_max_output_tokens")
    @classmethod
    def specialized_output_is_bounded(cls, value: int) -> int:
        if value < 500 or value > 16000:
            raise ValueError("SPECIALIZED_AGENT_MAX_OUTPUT_TOKENS must be from 500 to 16000")
        return value

    @field_validator("bid_comparison_max_bids")
    @classmethod
    def comparison_bid_count_is_bounded(cls, value: int) -> int:
        if value < 2 or value > 20:
            raise ValueError("BID_COMPARISON_MAX_BIDS must be from 2 to 20")
        return value

    @field_validator("bid_comparison_max_semantic_rows")
    @classmethod
    def comparison_rows_are_bounded(cls, value: int) -> int:
        if value < 1 or value > 30:
            raise ValueError("BID_COMPARISON_MAX_SEMANTIC_ROWS must be from 1 to 30")
        return value

    @field_validator("bid_comparison_max_output_tokens")
    @classmethod
    def comparison_output_is_bounded(cls, value: int) -> int:
        if value < 500 or value > 12000:
            raise ValueError("BID_COMPARISON_MAX_OUTPUT_TOKENS must be from 500 to 12000")
        return value

    @field_validator("qa_top_k")
    @classmethod
    def qa_top_k_is_bounded(cls, value: int) -> int:
        if value < 1 or value > 20:
            raise ValueError("QA_TOP_K must be from 1 to 20")
        return value

    @field_validator("qa_max_history_turns")
    @classmethod
    def qa_history_is_bounded(cls, value: int) -> int:
        if value < 0 or value > 20:
            raise ValueError("QA_MAX_HISTORY_TURNS must be from 0 to 20")
        return value

    @field_validator("qa_max_output_tokens")
    @classmethod
    def qa_output_is_bounded(cls, value: int) -> int:
        if value < 200 or value > 8000:
            raise ValueError("QA_MAX_OUTPUT_TOKENS must be from 200 to 8000")
        return value

    @field_validator("qa_max_passage_characters")
    @classmethod
    def qa_passage_is_bounded(cls, value: int) -> int:
        if value < 200 or value > 8000:
            raise ValueError("QA_MAX_PASSAGE_CHARACTERS must be from 200 to 8000")
        return value

    @field_validator("qa_max_compare_bids")
    @classmethod
    def qa_compare_bids_is_bounded(cls, value: int) -> int:
        if value < 0 or value > 3:
            raise ValueError("QA_MAX_COMPARE_BIDS must be from 0 to 3")
        return value

    @field_validator("qa_max_addendum_records", "qa_max_review_records")
    @classmethod
    def qa_record_limits_are_bounded(cls, value: int) -> int:
        if value < 0 or value > 50:
            raise ValueError("QA record limits must be from 0 to 50")
        return value

    @field_validator("human_review_max_reevaluation_attempts")
    @classmethod
    def reevaluation_attempts_are_bounded(cls, value: int) -> int:
        if value < 0 or value > 5:
            raise ValueError("HUMAN_REVIEW_MAX_REEVALUATION_ATTEMPTS must be from 0 to 5")
        return value

    @field_validator("human_review_api_token", mode="before")
    @classmethod
    def blank_review_token_is_unset(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator("upload_max_file_bytes")
    @classmethod
    def upload_size_is_bounded(cls, value: int) -> int:
        if value < 1024 or value > 1024 * 1024 * 1024:
            raise ValueError("UPLOAD_MAX_FILE_BYTES must be from 1024 to 1073741824")
        return value

    @field_validator("upload_max_files_per_request")
    @classmethod
    def upload_file_count_is_bounded(cls, value: int) -> int:
        if value < 1 or value > 100:
            raise ValueError("UPLOAD_MAX_FILES_PER_REQUEST must be from 1 to 100")
        return value

    @field_validator("upload_allowed_extensions")
    @classmethod
    def upload_extensions_are_supported(cls, value: str) -> str:
        extensions = {
            item.strip().lower() for item in value.split(",") if item.strip()
        }
        if not extensions:
            raise ValueError("UPLOAD_ALLOWED_EXTENSIONS must not be empty")
        unsupported = extensions - _PARSER_EXTENSIONS
        if unsupported:
            raise ValueError(
                "UPLOAD_ALLOWED_EXTENSIONS contains types with no parser: "
                + ", ".join(sorted(unsupported))
            )
        return ",".join(sorted(extensions))

    @field_validator("job_max_attempts")
    @classmethod
    def job_attempts_are_bounded(cls, value: int) -> int:
        if value < 1 or value > 10:
            raise ValueError("JOB_MAX_ATTEMPTS must be from 1 to 10")
        return value

    @field_validator(
        "job_poll_interval_seconds",
        "job_stale_after_seconds",
        "job_heartbeat_interval_seconds",
    )
    @classmethod
    def job_interval_is_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("job intervals must be greater than 0")
        return value

    @property
    def upload_extensions(self) -> frozenset[str]:
        return frozenset(self.upload_allowed_extensions.split(","))

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @field_validator("langgraph_checkpointer_backend")
    @classmethod
    def checkpointer_backend_is_supported(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"memory", "none"}:
            raise ValueError("LANGGRAPH_CHECKPOINTER_BACKEND must be memory or none")
        return normalized

    @field_validator("storage_path", "ingestion_input_root", mode="before")
    @classmethod
    def path_setting_is_present(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("path must not be blank")
        return value

    @field_validator("storage_path", "ingestion_input_root")
    @classmethod
    def path_setting_is_absolute(cls, value: Path) -> Path:
        """Resolve relative paths from the backend directory, not the process cwd."""
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = _BACKEND_ROOT / path
        return path.resolve()

    @model_validator(mode="after")
    def resolve_log_format(self) -> Self:
        """Use JSON logs in production and readable text logs in other environments."""
        chosen = self.log_format.strip().lower()
        if chosen == "auto":
            chosen = "json" if self.environment.strip().lower() == "production" else "text"
        if chosen not in {"text", "json"}:
            raise ValueError("LOG_FORMAT must be text, json, or auto")
        self.log_format = chosen
        return self

    @model_validator(mode="after")
    def chunk_settings_are_consistent(self) -> Self:
        """Chunk sizes are character counts. Overlap must leave room to move forward."""
        if self.chunk_size < 1:
            raise ValueError("CHUNK_SIZE must be a positive integer")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be zero or greater and smaller than CHUNK_SIZE")
        if self.table_chunk_max_rows < 1:
            raise ValueError("TABLE_CHUNK_MAX_ROWS must be a positive integer")
        if self.chunk_min_text_length < 1:
            raise ValueError("CHUNK_MIN_TEXT_LENGTH must be a positive integer")
        return self

    @model_validator(mode="after")
    def validation_thresholds_are_ordered(self) -> Self:
        if (
            self.validation_high_confidence_threshold
            <= self.validation_medium_confidence_threshold
        ):
            raise ValueError(
                "VALIDATION_HIGH_CONFIDENCE_THRESHOLD must be greater than "
                "VALIDATION_MEDIUM_CONFIDENCE_THRESHOLD"
            )
        return self

    def public_view(self) -> dict[str, object]:
        """Return settings that are safe to log or return from an API."""
        from app.core.redaction import redact_database_url

        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "environment": self.environment,
            "debug": self.debug,
            "api_prefix": self.api_prefix,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "request_timeout_seconds": self.request_timeout_seconds,
            "health_check_timeout_seconds": self.health_check_timeout_seconds,
            "storage_path": str(self.storage_path),
            "ingestion_input_root": str(self.ingestion_input_root),
            "database_url": redact_database_url(self.database_url),
            "qdrant_url": self.qdrant_url,
            "qdrant_api_key_set": self.qdrant_api_key is not None,
            "qdrant_collection_name": self.qdrant_collection_name,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "openai_api_key_set": self.openai_api_key is not None,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "embedding_batch_size": self.embedding_batch_size,
            "search_rrf_constant": self.search_rrf_constant,
            "search_allow_degraded": self.search_allow_degraded,
            "query_rewriting_enabled": self.query_rewriting_enabled,
            "query_rewrite_max_variants": self.query_rewrite_max_variants,
            "query_rewrite_timeout_seconds": self.query_rewrite_timeout_seconds,
            "reranking_enabled": self.reranking_enabled,
            "reranker_provider": self.reranker_provider,
            "reranker_model": self.reranker_model,
            "rerank_candidate_limit": self.rerank_candidate_limit,
            "rerank_batch_size": self.rerank_batch_size,
            "rerank_timeout_seconds": self.rerank_timeout_seconds,
            "rerank_max_input_characters": self.rerank_max_input_characters,
            "extraction_query_top_k": self.extraction_query_top_k,
            "extraction_max_evidence_chunks": self.extraction_max_evidence_chunks,
            "extraction_max_output_tokens": self.extraction_max_output_tokens,
            "addendum_max_chunks_per_document": self.addendum_max_chunks_per_document,
            "addendum_related_top_k": self.addendum_related_top_k,
            "addendum_max_evidence_chunks": self.addendum_max_evidence_chunks,
            "addendum_max_output_tokens": self.addendum_max_output_tokens,
            "validation_semantic_enabled": self.validation_semantic_enabled,
            "validation_high_confidence_threshold": self.validation_high_confidence_threshold,
            "validation_medium_confidence_threshold": self.validation_medium_confidence_threshold,
            "validation_max_output_tokens": self.validation_max_output_tokens,
            "langgraph_checkpointing_enabled": self.langgraph_checkpointing_enabled,
            "langgraph_checkpointer_backend": self.langgraph_checkpointer_backend,
            "langgraph_workflow_version": self.langgraph_workflow_version,
            "langgraph_workflow_timeout_seconds": self.langgraph_workflow_timeout_seconds,
            "specialized_agents_enabled": self.specialized_agents_enabled,
            "specialized_agent_timeout_seconds": self.specialized_agent_timeout_seconds,
            "specialized_agent_max_evidence_chunks": self.specialized_agent_max_evidence_chunks,
            "specialized_agent_max_output_tokens": self.specialized_agent_max_output_tokens,
            "bid_comparison_max_bids": self.bid_comparison_max_bids,
            "bid_comparison_semantic_timeout_seconds": self.bid_comparison_semantic_timeout_seconds,
            "bid_comparison_max_semantic_rows": self.bid_comparison_max_semantic_rows,
            "bid_comparison_max_output_tokens": self.bid_comparison_max_output_tokens,
            "evaluation_exact_identifier_min": self.evaluation_exact_identifier_min,
            "evaluation_invalid_citation_rejection_min": self.evaluation_invalid_citation_rejection_min,
            "evaluation_conflict_recall_min": self.evaluation_conflict_recall_min,
            "qa_top_k": self.qa_top_k,
            "qa_max_history_turns": self.qa_max_history_turns,
            "qa_max_output_tokens": self.qa_max_output_tokens,
            "qa_max_passage_characters": self.qa_max_passage_characters,
            "qa_max_compare_bids": self.qa_max_compare_bids,
            "qa_max_addendum_records": self.qa_max_addendum_records,
            "qa_max_review_records": self.qa_max_review_records,
            "human_review_enabled": self.human_review_enabled,
            "human_review_confidence_threshold": self.human_review_confidence_threshold,
            "human_review_critical_fields": self.human_review_critical_fields,
            "human_review_max_reevaluation_attempts": self.human_review_max_reevaluation_attempts,
            "human_review_api_token_set": self.human_review_api_token is not None,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "table_chunk_max_rows": self.table_chunk_max_rows,
            "chunk_min_text_length": self.chunk_min_text_length,
            "upload_max_file_bytes": self.upload_max_file_bytes,
            "upload_max_files_per_request": self.upload_max_files_per_request,
            "upload_allowed_extensions": self.upload_allowed_extensions,
            "job_worker_embedded": self.job_worker_embedded,
            "job_poll_interval_seconds": self.job_poll_interval_seconds,
            "job_max_attempts": self.job_max_attempts,
            "job_stale_after_seconds": self.job_stale_after_seconds,
            "job_heartbeat_interval_seconds": self.job_heartbeat_interval_seconds,
            "cors_allowed_origins": self.cors_origins,
        }


settings = Settings()
