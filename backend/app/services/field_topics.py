"""Shared assignment-field to requirement-topic matching."""

FIELD_TOPIC_KEYWORDS: dict[str, set[str]] = {
    "Bid Number": {"bid", "solicitation", "reference", "number"},
    "Due Date": {"due", "deadline", "submission"},
    "Term of Bid": {"term", "renewal", "duration"},
    "Delivery Date": {"delivery", "deadline"},
    "Additional Documentation Required": {"document", "form", "attachment"},
    "Model_no": {"model", "product", "specification"},
    "Part_no": {"part", "product", "specification"},
    "Product Specification": {"product", "specification", "warranty"},
    "Payment Terms": {"payment", "invoice"},
}


def field_matches_topic(field_name: str, topic: str) -> bool:
    """Match broad requirement concepts without bid-specific values."""
    expected = FIELD_TOPIC_KEYWORDS.get(
        field_name,
        {word.lower() for word in field_name.replace("_", " ").split()},
    )
    actual = {word.strip(".,:;()").lower() for word in topic.split()}
    return bool(expected & actual)
