"""Narrative chunking. These tests do not use PostgreSQL."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.ingestion.chunking.text_chunker import TextChunker


def _chunker(size: int = 20, overlap: int = 5, minimum: int = 1) -> TextChunker:
    return TextChunker(size, overlap, minimum)


def test_short_text_is_one_chunk() -> None:
    assert _chunker().split("Short note.") == ["Short note."]


def test_empty_text_produces_no_chunks() -> None:
    assert _chunker().split("   \n\t") == []
    assert _chunker().split("") == []


def test_overlap_is_exact_when_there_is_no_boundary() -> None:
    text = "a" * 50
    chunks = _chunker(size=20, overlap=5).split(text)

    assert [len(chunk) for chunk in chunks] == [20, 20, 20]
    cursor = 0
    for index, chunk in enumerate(chunks):
        assert text[cursor : cursor + len(chunk)] == chunk
        if index < len(chunks) - 1:
            cursor += len(chunk) - 5
        else:
            assert cursor + len(chunk) == len(text)
    assert chunks[1].startswith(chunks[0][-5:])


def test_paragraph_and_sentence_boundaries_are_preferred() -> None:
    paragraph = "A" * 10 + "\n\n" + "B" * 40
    chunks = _chunker(size=30, overlap=5).split(paragraph)
    assert chunks[0] == "A" * 10 + "\n\n"
    assert chunks[1].startswith(chunks[0][-5:])
    _assert_coverage(paragraph, chunks, overlap=5)

    sentence = "Hello world. " + "C" * 40
    sentence_chunks = _chunker(size=20, overlap=4).split(sentence)
    assert sentence_chunks[0] == "Hello world. "
    _assert_coverage(sentence, sentence_chunks, overlap=4)


def test_an_oversized_token_is_split_without_losing_characters() -> None:
    text = "Supercalifragilistic" + "xyz"
    chunks = _chunker(size=10, overlap=2).split(text)
    assert all(len(chunk) <= 10 for chunk in chunks[:-1])
    _assert_coverage(text, chunks, overlap=2)


def test_short_real_text_is_kept_when_it_is_below_the_minimum() -> None:
    assert _chunker(size=100, overlap=10, minimum=50).split("Hi") == ["Hi"]


def test_chunk_settings_reject_an_overlap_that_cannot_advance() -> None:
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP"):
        Settings(_env_file=None, chunk_size=10, chunk_overlap=10)
    with pytest.raises(ValidationError, match="CHUNK_SIZE"):
        Settings(_env_file=None, chunk_size=0)
    loaded = Settings(_env_file=None)
    assert loaded.chunk_size == 1000
    assert loaded.chunk_overlap == 150
    assert loaded.table_chunk_max_rows == 20
    assert loaded.chunk_min_text_length == 50


def _assert_coverage(text: str, chunks: list[str], overlap: int) -> None:
    cursor = 0
    for index, chunk in enumerate(chunks):
        assert text[cursor : cursor + len(chunk)] == chunk
        if index == len(chunks) - 1:
            assert cursor + len(chunk) == len(text)
        else:
            cursor += len(chunk) - overlap
