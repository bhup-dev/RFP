"""Table chunking. Cell values are copied and missing cells stay empty."""

from app.ingestion.chunking.table_chunker import TableChunker
from app.schemas.parsing import ParseMessage, ParsedTable


def _table(**kwargs: object) -> ParsedTable:
    values = {
        "table_id": "table-1",
        "page_number": 2,
        "section_title": "Pricing",
        "anchor": "pricing",
        "headers": ["Part", "Qty"],
        "rows": [["PN-100", "2"], ["PN-200", None]],
        "text": "",
    }
    values.update(kwargs)
    return ParsedTable(**values)


def test_headers_repeat_and_row_order_is_preserved() -> None:
    pieces = TableChunker(max_rows=20).split(_table())

    assert len(pieces) == 1
    assert pieces[0].text == "Part | Qty\nPN-100 | 2\nPN-200 | "
    assert pieces[0].table_id == "table-1"
    assert "PN-100" in pieces[0].text
    assert pieces[0].row_start == 0
    assert pieces[0].row_count == 2


def test_large_tables_split_into_row_groups() -> None:
    rows = [[f"PN-{index}", str(index)] for index in range(25)]
    pieces = TableChunker(max_rows=20).split(_table(rows=rows))

    assert [piece.row_count for piece in pieces] == [20, 5]
    assert pieces[0].text.startswith("Part | Qty\nPN-0 | 0\n")
    assert "PN-19 | 19" in pieces[0].text
    assert pieces[1].text.startswith("Part | Qty\nPN-20 | 20\n")
    assert pieces[1].text.endswith("PN-24 | 24")
    assert "PN-0" not in pieces[1].text.split("\n", 1)[1]


def test_missing_headers_and_ragged_rows_are_explicit() -> None:
    missing = TableChunker(1).split(_table(headers=None, rows=[["only-cell"]]))
    assert missing[0].text == "only-cell"
    assert missing[0].warnings == ()

    ragged = TableChunker(20).split(_table(rows=[["PN-100"]]))
    assert ragged[0].text == "Part | Qty\nPN-100"
    assert ragged[0].warnings[0].code == "ragged_table_row"
    assert "PN-100" in ragged[0].text


def test_ambiguous_parser_warnings_are_copied() -> None:
    table = _table(
        warnings=[
            ParseMessage(
                code="ambiguous_table_span",
                message="table span overlapped an existing cell; cell text was kept",
                page_number=2,
            )
        ]
    )
    pieces = TableChunker(20).split(table)
    assert pieces[0].warnings[0].code == "ambiguous_table_span"
    assert pieces[0].warnings[0].table_id == "table-1"
    assert pieces[0].warnings[0].page_number == 2
