"""Local HTML extraction. This parser does not fetch URLs or run JavaScript."""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, Comment, FeatureNotFound, NavigableString, Tag

from app.ingestion.parsers.base import (
    RegisteredSource,
    assemble_document,
    failed_document,
    table_text,
)
from app.schemas.parsing import ParseMessage, ParseMetadata, ParseStatus, ParsedDocument, ParsedSection, ParsedTable

PARSER_NAME = "beautifulsoup"
REMOVED_TAGS = {"script", "style", "noscript", "template", "iframe", "svg", "nav"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
MAX_SPAN = 25
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")


class HtmlParser:
    """Extract headings, prose, lists, and tables from a local HTML file."""

    name = PARSER_NAME

    def __init__(self) -> None:
        from importlib.metadata import version

        self.version = version("beautifulsoup4")

    def parse(self, path: Path, source: RegisteredSource) -> ParsedDocument:
        try:
            raw = path.read_bytes()
        except OSError:
            return failed_document(
                source,
                code="unreadable",
                message="HTML file could not be read",
                parser_name=self.name,
                parser_version=self.version,
                document_type="html",
            )
        text = raw.decode("utf-8-sig", errors="replace")
        warnings: list[ParseMessage] = []
        if "\ufffd" in text:
            warnings.append(
                ParseMessage(
                    code="encoding_replaced",
                    message="some characters could not be decoded as UTF-8",
                )
            )
        try:
            soup = BeautifulSoup(text, "lxml")
        except FeatureNotFound:
            soup = BeautifulSoup(text, "html.parser")
            warnings.append(
                ParseMessage(code="html_parser_fallback", message="HTML was parsed without lxml")
            )
        for tag in soup.find_all(list(REMOVED_TAGS)):
            tag.decompose()

        builder = _HtmlDocument()
        root = soup.body or soup
        for child in list(root.children):
            builder.walk(child)
        if not builder.sections:
            builder.start_section(None)
        sections = builder.finish()
        document_text = _document_text(sections)
        if not document_text:
            warnings.append(ParseMessage(code="empty_document", message="HTML has no extractable text"))
        return assemble_document(
            source,
            parser_name=self.name,
            parser_version=self.version,
            document_type="html",
            sections=sections,
            warnings=[*warnings, *(message for section in sections for message in section.warnings)],
            errors=[],
            text=document_text,
            status=ParseStatus.PARSED,
            metadata=ParseMetadata(),
        )


class _SectionDraft:
    def __init__(self, title: str | None, level: int | None, anchor: str | None) -> None:
        self.title = title
        self.level = level
        self.anchor = anchor
        self.parts: list[str] = []
        self.tables: list[ParsedTable] = []
        self.warnings: list[ParseMessage] = []
        self._inline = False

    def add_inline(self, text: str) -> None:
        if self._inline and self.parts:
            self.parts[-1] = f"{self.parts[-1]} {text}"
        else:
            self.parts.append(text)
        self._inline = True

    def add_block(self, text: str) -> None:
        self.parts.append(text)
        self._inline = False

    def finish(self) -> ParsedSection:
        return ParsedSection(
            section_title=self.title,
            heading_level=self.level,
            anchor=self.anchor,
            text="\n".join(self.parts).strip(),
            tables=self.tables,
            warnings=self.warnings,
        )


class _HtmlDocument:
    def __init__(self) -> None:
        self.sections: list[_SectionDraft] = []
        self._current: _SectionDraft | None = None
        self._table_count = 0

    def start_section(self, tag: Tag | None) -> None:
        if tag is None:
            self._current = _SectionDraft(None, None, None)
        else:
            self._current = _SectionDraft(
                _normalize(tag.get_text(" ", strip=True)) or None,
                int(tag.name[1]),
                _anchor(tag),
            )
        self.sections.append(self._current)

    def walk(self, node: object) -> None:
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            text = _normalize(str(node))
            if text:
                self._ensure().add_inline(text)
            return
        if not isinstance(node, Tag):
            return
        name = (node.name or "").lower()
        if name in REMOVED_TAGS or name in {"head", "meta", "link"}:
            return
        if name in HEADINGS:
            self.start_section(node)
            return
        if name == "table":
            self._add_tables(node)
            return
        if name in {"ul", "ol"}:
            self._add_list(node)
            return
        if name in {"p", "pre", "blockquote", "figcaption", "address"}:
            if node.find("table") or node.find(["ul", "ol"]):
                for child in list(node.children):
                    self.walk(child)
            else:
                text = _normalize(node.get_text(" ", strip=True))
                if text:
                    self._ensure().add_block(text)
            return
        for child in list(node.children):
            self.walk(child)

    def finish(self) -> list[ParsedSection]:
        return [section.finish() for section in self.sections]

    def _ensure(self) -> _SectionDraft:
        if self._current is None:
            self.start_section(None)
        assert self._current is not None
        return self._current

    def _add_list(self, node: Tag) -> None:
        ordered = node.name == "ol"
        index = _list_start(node) if ordered else 1
        for item in node.find_all("li", recursive=False):
            text = _list_item_text(item)
            if text:
                prefix = f"{index}. " if ordered else "- "
                self._ensure().add_block(f"{prefix}{text}")
            if ordered:
                index += 1
            for table in item.find_all("table", recursive=False):
                self._add_tables(table)
            for nested in item.find_all(["ul", "ol"], recursive=False):
                self._add_list(nested)

    def _add_tables(self, table: Tag) -> None:
        tables = [table, *table.find_all("table")]
        for offset, item in enumerate(tables):
            self._table_count += 1
            warnings: list[ParseMessage] = []
            if offset:
                warnings.append(
                    ParseMessage(
                        code="nested_table",
                        message="nested table extracted separately",
                        anchor=_anchor(item),
                    )
                )
            parsed = _build_table(item, f"table-{self._table_count}", self._current, warnings)
            current = self._ensure()
            parsed.section_title = current.title
            parsed.anchor = parsed.anchor or current.anchor
            current.tables.append(parsed)
            current.warnings.extend(parsed.warnings)


def _build_table(
    table: Tag,
    table_id: str,
    section: _SectionDraft | None,
    warnings: list[ParseMessage],
) -> ParsedTable:
    row_tags = _own_rows(table)
    grid, grid_warnings = _grid(row_tags)
    warnings.extend(grid_warnings)
    headers, rows = _split_header(table, row_tags, grid, warnings)
    if not row_tags:
        warnings.append(ParseMessage(code="empty_table", message="table has no rows", anchor=_anchor(table)))
    return ParsedTable(
        table_id=table_id,
        section_title=section.title if section is not None else None,
        anchor=_anchor(table),
        headers=headers,
        rows=rows,
        text=table_text(headers, rows),
        warnings=warnings,
    )


def _own_rows(table: Tag) -> list[Tag]:
    rows: list[Tag] = []
    for child in table.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "tr":
            rows.append(child)
        elif child.name in {"thead", "tbody", "tfoot"}:
            rows.extend(child.find_all("tr", recursive=False))
    return rows


def _split_header(
    table: Tag,
    row_tags: list[Tag],
    grid: list[list[str | None]],
    warnings: list[ParseMessage],
) -> tuple[list[str | None] | None, list[list[str | None]]]:
    thead = table.find("thead")
    header_rows = thead.find_all("tr", recursive=False) if isinstance(thead, Tag) else []
    use_header = False
    if header_rows:
        cells = [cell for row in header_rows for cell in row.find_all(["th", "td"], recursive=False)]
        if cells and all(cell.name == "th" for cell in cells) and len(header_rows) == 1:
            use_header = True
        elif len(header_rows) > 1:
            warnings.append(
                ParseMessage(
                    code="complex_header",
                    message="table header has multiple rows and was kept as data",
                    anchor=_anchor(table),
                )
            )
    elif row_tags:
        first_cells = row_tags[0].find_all(["th", "td"], recursive=False)
        use_header = bool(first_cells) and all(cell.name == "th" for cell in first_cells)
    if use_header and grid and any(grid[0]):
        return grid[0], grid[1:]
    return None, grid


def _grid(row_tags: list[Tag]) -> tuple[list[list[str | None]], list[ParseMessage]]:
    warnings: list[ParseMessage] = []
    occupied: dict[tuple[int, int], str | None] = {}
    max_col = 0
    for row_index, row in enumerate(row_tags):
        column = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while (row_index, column) in occupied:
                column += 1
            value = _normalize(cell.get_text(" ", strip=True)) or None
            rowspan, colspan = _span(cell, warnings)
            for down in range(rowspan):
                for across in range(colspan):
                    position = (row_index + down, column + across)
                    if position in occupied:
                        warnings.append(
                            ParseMessage(
                                code="ambiguous_table_span",
                                message="table span overlapped an existing cell; cell text was kept",
                            )
                        )
                        if down == 0 and across == 0:
                            extra = column
                            while (row_index, extra) in occupied:
                                extra += 1
                            occupied[(row_index, extra)] = value
                            max_col = max(max_col, extra + 1)
                        continue
                    occupied[position] = value if down == 0 and across == 0 else None
                    max_col = max(max_col, column + across + 1)
            column += colspan
    if not occupied:
        return [], warnings
    last_row = max(row for row, _column in occupied)
    grid = [[occupied.get((row, column)) for column in range(max_col)] for row in range(last_row + 1)]
    return grid, warnings


def _span(cell: Tag, warnings: list[ParseMessage]) -> tuple[int, int]:
    return _one_span(cell.get("rowspan"), warnings), _one_span(cell.get("colspan"), warnings)


def _one_span(raw: object, warnings: list[ParseMessage]) -> int:
    if raw is None:
        return 1
    try:
        value = int(str(raw).strip())
    except ValueError:
        warnings.append(ParseMessage(code="invalid_table_span", message="table span was not a number"))
        return 1
    if value < 1 or value > MAX_SPAN:
        warnings.append(
            ParseMessage(
                code="ambiguous_table_span",
                message="table span could not be applied; the cell text was kept",
            )
        )
        return 1
    return value


def _list_item_text(item: Tag) -> str:
    clone = BeautifulSoup(str(item), "lxml")
    node = clone.find("li")
    if not isinstance(node, Tag):
        return ""
    for tag in node.find_all(["table", "ul", "ol"]):
        tag.decompose()
    return _normalize(node.get_text(" ", strip=True))


def _list_start(node: Tag) -> int:
    raw = node.get("start")
    if raw is None:
        return 1
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 1
    return value if value > 0 else 1


def _anchor(tag: Tag) -> str | None:
    if tag.get("id"):
        return str(tag["id"])
    link = tag.find("a")
    if isinstance(link, Tag):
        if link.get("id"):
            return str(link["id"])
        if link.get("name"):
            return str(link["name"])
    return None


def _normalize(value: str) -> str:
    text = value.replace("\xa0", " ")
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


def _document_text(sections: list[ParsedSection]) -> str:
    chunks: list[str] = []
    for section in sections:
        if section.section_title:
            chunks.append(section.section_title)
        if section.text:
            chunks.append(section.text)
        chunks.extend(table.text for table in section.tables if table.text)
    return "\n\n".join(chunks)
