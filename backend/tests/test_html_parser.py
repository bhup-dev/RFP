"""HTML parser tests. Files are local and no network calls are expected."""

import hashlib
import socket
from pathlib import Path
from uuid import uuid4

import pytest

from app.ingestion.parsers.base import RegisteredSource
from app.ingestion.parsers.html_parser import HtmlParser
from app.schemas.parsing import ParseStatus


def _source(path: Path, root: Path) -> RegisteredSource:
    return RegisteredSource(
        document_id=uuid4(),
        relative_path=path.relative_to(root).as_posix(),
        file_name=path.name,
        document_type="html",
    )


def _parse(path: Path, html: str) -> object:
    path.write_text(html, encoding="utf-8")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    parsed = HtmlParser().parse(path, _source(path, path.parent))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    return parsed


def test_headings_paragraphs_lists_and_entities(tmp_path: Path) -> None:
    path = tmp_path / "portal.html"
    parsed = _parse(
        path,
        """
        <html><body>
          <h1 id="top">Solicitation</h1>
          <p>Fish &amp; Chips&nbsp;today</p>
          <h2 id="scope">Scope</h2>
          <p>Delivery details</p>
          <ul><li>First item</li><li>Second item</li></ul>
          <ol start="3"><li>Alpha</li><li>Beta</li></ol>
        </body></html>
        """,
    )

    assert parsed.status == ParseStatus.PARSED
    assert parsed.parser_name == "beautifulsoup"
    assert [section.page_number for section in parsed.sections] == [None, None]
    assert parsed.sections[0].section_title == "Solicitation"
    assert parsed.sections[0].heading_level == 1
    assert parsed.sections[0].anchor == "top"
    assert "Fish & Chips today" in parsed.sections[0].text
    assert parsed.sections[1].section_title == "Scope"
    assert parsed.sections[1].heading_level == 2
    assert parsed.sections[1].anchor == "scope"
    assert "Delivery details" in parsed.sections[1].text
    assert "- First item" in parsed.sections[1].text
    assert "- Second item" in parsed.sections[1].text
    assert "3. Alpha" in parsed.sections[1].text
    assert "4. Beta" in parsed.sections[1].text
    assert "Solicitation" in parsed.text
    assert parsed.sections[0].text != parsed.text


def test_table_headers_spans_and_ambiguous_layout(tmp_path: Path) -> None:
    path = tmp_path / "forms.html"
    parsed = _parse(
        path,
        """
        <html><body>
          <h2 id="pricing">Pricing</h2>
          <table id="items">
            <tr><th>Item</th><th>Qty</th><th>Notes</th></tr>
            <tr><td rowspan="2">Cable</td><td>1</td><td>Red</td></tr>
            <tr><td colspan="2">See drawing</td></tr>
          </table>
          <table id="overlap">
            <tr><th>H1</th><th>H2</th></tr>
            <tr><td>A</td><td rowspan="2">B</td></tr>
            <tr><td colspan="2">C</td></tr>
          </table>
        </body></html>
        """,
    )

    items = next(table for table in parsed.tables if table.anchor == "items")
    assert items.table_id == "table-1"
    assert items.page_number is None
    assert items.section_title == "Pricing"
    assert items.headers == ["Item", "Qty", "Notes"]
    assert items.rows[0] == ["Cable", "1", "Red"]
    assert items.rows[1][0] is None
    assert items.rows[1][1] == "See drawing"
    assert "Cable" in items.text
    assert all(item.code != "ambiguous_table_span" for item in items.warnings)
    assert items in parsed.sections[0].tables

    overlap = next(table for table in parsed.tables if table.anchor == "overlap")
    flat = [cell for row in overlap.rows for cell in row]
    assert "A" in flat and "B" in flat and "C" in flat
    assert any(item.code == "ambiguous_table_span" for item in overlap.warnings)
    assert overlap.headers == ["H1", "H2"]


def test_scripts_styles_and_malformed_markup_stay_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def reject_network(*args: object, **kwargs: object) -> object:
        calls.append(args)
        raise AssertionError("network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    path = tmp_path / "portal.html"
    parsed = _parse(
        path,
        """
        <html><body>
          <script src="https://cdn.example/app.js">alert("secret-script")</script>
          <style>body { color: red; }</style>
          <nav>Home link</nav>
          <p>Hello <b>world</p>
          <p>Next &amp; more</p>
          <img src="http://example.com/diagram.png" alt="Diagram">
          <a href="https://example.com/bid" id="bid-link">Bid link</a>
        </body></html>
        """,
    )

    assert calls == []
    assert parsed.status == ParseStatus.PARSED
    assert "secret-script" not in parsed.text
    assert "color: red" not in parsed.text
    assert "Home link" not in parsed.text
    assert "cdn.example" not in parsed.text
    assert "Hello world" in parsed.text
    assert "Next & more" in parsed.text
    assert "Bid link" in parsed.text
    assert "example.com" not in parsed.text
    assert str(tmp_path) not in parsed.text
