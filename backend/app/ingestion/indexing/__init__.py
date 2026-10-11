"""Embed persisted chunks and upsert them to Qdrant."""

from app.ingestion.indexing.indexing_service import IndexingService

__all__ = ["IndexingService"]
