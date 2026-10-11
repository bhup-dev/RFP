"""Split narrative text on paragraph and sentence boundaries.

Chunks stay inside one parsed section. Overlap is an exact character count:
the next chunk starts that many characters before the previous chunk ends.
"""

from __future__ import annotations

_PARAGRAPH = "\n\n"
_SENTENCES = (". ", "! ", "? ", ".\n", "!\n", "?\n")


class TextChunker:
    """Character chunker. It copies source text and does not summarize it."""

    def __init__(self, chunk_size: int, overlap: int, min_length: int) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be a positive integer")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be zero or greater and smaller than chunk_size")
        if min_length < 1:
            raise ValueError("min_length must be a positive integer")
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._min_length = min_length

    def split(self, text: str) -> list[str]:
        """Return ordered chunks. Whitespace-only input returns an empty list."""
        normalized = text.strip()
        if not normalized:
            return []
        if len(normalized) <= self._chunk_size:
            return [normalized]

        chunks: list[str] = []
        start = 0
        length = len(normalized)
        while start < length:
            hard_end = min(start + self._chunk_size, length)
            end = hard_end if hard_end == length else _choose_end(
                normalized,
                start,
                hard_end,
                self._overlap,
            )
            piece = normalized[start:end]
            if piece.strip():
                # Pieces shorter than the configured minimum are kept so source text is not dropped.
                chunks.append(piece)
            if end >= length:
                break
            next_start = end - self._overlap
            if next_start <= start:
                next_start = end
            start = next_start
        return chunks


def _choose_end(text: str, start: int, hard_end: int, overlap: int) -> int:
    """Pick the rightmost preferred boundary that still moves the window forward."""
    earliest_end = start + overlap + 1
    if earliest_end > hard_end:
        return hard_end
    paragraph = _token_end(text, _PARAGRAPH, earliest_end, hard_end)
    if paragraph is not None:
        return paragraph
    sentence_ends = [
        end
        for token in _SENTENCES
        if (end := _token_end(text, token, earliest_end, hard_end)) is not None
    ]
    if sentence_ends:
        return max(sentence_ends)
    for index in range(hard_end - 1, earliest_end - 2, -1):
        if text[index].isspace():
            return index + 1
    return hard_end


def _token_end(text: str, token: str, earliest_end: int, hard_end: int) -> int | None:
    index = text.rfind(token, 0, hard_end)
    if index == -1:
        return None
    end = index + len(token)
    if earliest_end <= end <= hard_end:
        return end
    return None
