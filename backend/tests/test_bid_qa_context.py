"""Read-only Q&A context helpers: record rendering, query expansion, and request detection."""

import pytest

from app.db.models.addendum import AddendumChange
from app.db.models.review import HumanReviewItem
from app.services.bid_qa_context import (
    addendum_record,
    bid_label,
    expand_query,
    is_profile_request,
    needs_policy_notes,
    profile_records,
    review_record,
)
from tests.test_bid_qa import BID, _citation, _profile


def _citation_row(excerpt: str) -> dict:
    return _citation(excerpt).model_dump(mode="json")


def test_profile_records_show_status_and_contradicting_values():
    records = profile_records(_profile())

    assert len(records) == 20
    due = records[2]
    assert due.text.startswith('Profile field "Due Date": value=July 9, 2024 at 2:00 PM CST; status=needs_review')
    assert 'contradicting evidence: rfp.pdf page 2: "Solicitation Due 27-JUN-2024 14:00:00"' in due.text
    assert [citation.file_name for citation in due.citations] == ["addendum2.pdf", "rfp.pdf"]
    assert records[1].text == 'Profile field "Title": value=null; status=not_found'


def test_long_contradicting_quote_is_centred_on_the_due_date():
    profile = _profile()
    excerpt = "Terms and conditions apply. " * 20 + "Solicitation Due 27-JUN-2024 14:00:00 Central"
    profile.fields["Due Date"].contradicting_citations[0] = _citation(excerpt)

    text = profile_records(profile)[2].text
    assert "Solicitation Due 27-JUN-2024 14:00:00" in text


def test_addendum_record_lists_values_and_parses_sources():
    row = AddendumChange(
        bid_id=BID,
        field_name="Due Date",
        original_value="June 27, 2024",
        amended_value="July 9, 2024",
        status="pending",
        severity="high",
        notes="Addendum 2 extends the due date.",
        sources=[_citation_row("July 9, 2024"), {"not": "a citation"}],
    )
    record = addendum_record(row)

    assert record.text == (
        'Addendum finding (pending) on "Due Date"; original=June 27, 2024; amended=July 9, 2024; '
        "severity=high; explanation=Addendum 2 extends the due date."
    )
    assert len(record.citations) == 1


def test_review_record_describes_the_open_item():
    row = HumanReviewItem(
        bid_id=BID,
        category="conflicting_values",
        severity="high",
        status="pending",
        field_name="Due Date",
        proposed_value="July 9, 2024",
        validation_status="conflicting",
        reason="Two due dates are cited.",
        supporting_citations=[_citation_row("July 9, 2024")],
        contradicting_citations=[_citation_row("27-JUN-2024")],
    )
    record = review_record(row)

    assert record.text.startswith("Open review item (conflicting_values, severity high, status pending)")
    assert 'field "Due Date"' in record.text
    assert "reason=Two due dates are cited." in record.text
    assert len(record.citations) == 2


def test_bid_label_falls_back_to_profile_then_identifier():
    assert bid_label("bid6", None, _profile()) == "JA-207652"
    assert bid_label("bid6", None, None) == "bid6"


@pytest.mark.parametrize(
    ("question", "terms"),
    [
        ("What was the original deadline?", ["solicitation due", "closing date", "issue date"]),
        ("When is delivery required?", ["days of award"]),
        ("What is the buyer's phone number?", ["telephone"]),
        ("Is a bond required?", ["bid bond", "surety"]),
        ("How do invoices get paid?", ["payment terms"]),
    ],
)
def test_expand_query_adds_procurement_terms(question, terms):
    expanded = expand_query(question)
    assert expanded.startswith(f"{question}\n")
    for term in terms:
        assert term in expanded


def test_expand_query_leaves_unrelated_questions_alone():
    assert expand_query("What products are requested?") == "What products are requested?"
    assert expand_query("x " * 1500).endswith("x ")


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Extract all 20 required fields as JSON", True),
        ("Give me the twenty fields", True),
        ("Bid Number, Title, Due Date, Payment Terms, contact_info and Model_no please", True),
        ("What are the payment terms and due date?", False),
        ("When are proposals due?", False),
    ],
)
def test_profile_request_detection(question, expected):
    assert is_profile_request(question) is expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Can the agent approve this bid?", True),
        ("What happens after I request re-evaluation?", True),
        ("Why is the due date not validated?", True),
        ("When are proposals due?", False),
        ("What is the delivery schedule?", False),
    ],
)
def test_policy_note_trigger(question, expected):
    assert needs_policy_notes(question) is expected


def test_records_do_not_change_their_inputs():
    profile = _profile()
    before = profile.model_dump()
    profile_records(profile)
    assert profile.model_dump() == before
