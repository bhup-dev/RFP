"""Split parsed tables into row groups that each repeat the header."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.chunking import ChunkMessage
from app.schemas.parsing import ParsedTable


@dataclass(frozen=True)
class TablePiece:
    """One independently readable slice of a table."""

    text: str
    table_id: str
    row_start: int
    row_count: int
    warnings: tuple[ChunkMessage, ...]


class TableChunker:
    """Group table rows. A row is never split, and missing cells stay empty."""

    def __init__(self, max_rows: int) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be a positive integer")
        self._max_rows = max_rows

    def split(self, table: ParsedTable) -> list[TablePiece]:
        if table.headers is None and not table.rows:
            return []
        warnings = _warnings(table)
        if not table.rows:
            return [
                TablePiece(
                    text=_render(table.headers, []),
                    table_id=table.table_id,
                    row_start=0,
                    row_count=0,
                    warnings=warnings,
                )
            ]
        pieces: list[TablePiece] = []
        for start in range(0, len(table.rows), self._max_rows):
            group = table.rows[start : start + self._max_rows]
            pieces.append(
                TablePiece(
                    text=_render(table.headers, group),
                    table_id=table.table_id,
                    row_start=start,
                    row_count=len(group),
                    warnings=warnings,
                )
            )
        return pieces


def _warnings(table: ParsedTable) -> tuple[ChunkMessage, ...]:
    messages = [
        ChunkMessage(
            code=item.code,
            message=item.message,
            page_number=item.page_number or table.page_number,
            table_id=table.table_id,
        )
        for item in table.warnings
    ]
    if table.headers is not None:
        width = len(table.headers)
        if any(len(row) != width for row in table.rows):
            messages.append(
                ChunkMessage(
                    code="ragged_table_row",
                    message="a table row does not match the header width; cells were not padded",
                    page_number=table.page_number,
                    table_id=table.table_id,
                )
            )
    return tuple(messages)


def _render(headers: list[str | None] | None, rows: list[list[str | None]]) -> str:
    lines: list[str] = []
    if headers is not None:
        lines.append(_line(headers))
    lines.extend(_line(row) for row in rows)
    return "\n".join(lines)


def _line(cells: list[str | None]) -> str:
    return " | ".join("" if cell is None else cell for cell in cells)
