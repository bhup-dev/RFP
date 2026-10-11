# Retrieval evaluation data

`retrieval_queries.json` is intentionally empty. No Bid1 or Bid2 source
documents, registered bids, or persisted chunks were present when Task 2.5
was implemented, so adding expected results would fabricate ground truth.

Populate `queries` only after checking the actual source pages or sections:

```json
{
  "query_id": "bid1-payment-terms",
  "query": "What are the payment terms?",
  "bid_identifier": "Bid1",
  "relevant": [
    {
      "file_name": "verified-source.pdf",
      "page_number": 4,
      "grade": 2
    }
  ],
  "expect_no_results": false
}
```

Use `chunk_id` when chunk ids are stable. Use `file_name` with a verified
`page_number` or `section_title` when re-chunking can change ids. A query with
unknown ground truth has an empty `relevant` list and is excluded from metric
averages. Set `expect_no_results` only after verifying that no source is
relevant.

The application does not invent evaluation scores. Load this file with
`app.retrieval.evaluation.load_dataset`, run each query through the retrieval
service, and pass the result mapping to `evaluate`.
