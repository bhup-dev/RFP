# Retrieval evaluation data

`retrieval_queries.json` holds 41 labelled questions: 21 for Bid1 (Dallas ISD
JA-207652) and 20 for Bid2 (Maryland #E20P4600040). Each question lists its
relevant chunks with a grade (2 = states the answer, 1 = related context) and
the quoted evidence.

The labels were made by the Cursor agent (an AI assistant) by reading the
stored chunk text in PostgreSQL, not by a human domain expert, and without
looking at search results:

1. `retrieval_label_spec.json` lists each question with regular-expression
   patterns for the answer text found in the documents.
2. `python -m scripts.build_retrieval_ground_truth BID1_ID BID2_ID` matches
   those patterns against every indexed chunk of the bid and writes
   `retrieval_queries.json`. A question with no matching chunk is an error,
   never an empty label.

Chunk ids are stable for the current index. If the documents are re-chunked,
re-run step 2.

To measure every retrieval configuration (keyword only, semantic only, hybrid
RRF, hybrid with LLM query rewriting, and hybrid with the chat's synonym
expansion):

```bash
python -m scripts.retrieval_eval --output-dir ../deliverables/retrieval_evaluation
```

This needs the live PostgreSQL, Qdrant and OpenAI services. Reranking is
reported as not run while `sentence_transformers` is not installed. The
application never invents evaluation scores; every number comes from a
measured run.

`task_2_7_report.json` predates the labelled set and has no metrics. The current
results are in `deliverables/retrieval_evaluation/`.
