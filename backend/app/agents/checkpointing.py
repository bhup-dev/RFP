"""Configured LangGraph checkpointer lifecycle."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings, settings

_memory_saver: InMemorySaver | None = None


def get_checkpointer(
    config: Settings = settings,
) -> BaseCheckpointSaver | None:
    """Return one process-wide saver; memory checkpoints are not durable."""
    global _memory_saver
    if (
        not config.langgraph_checkpointing_enabled
        or config.langgraph_checkpointer_backend == "none"
    ):
        return None
    if _memory_saver is None:
        _memory_saver = InMemorySaver()
    return _memory_saver
