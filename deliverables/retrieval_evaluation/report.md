# Retrieval evaluation report

Generated 2026-10-11T13:32:23+00:00 by `backend/scripts/retrieval_eval.py` against the live PostgreSQL and Qdrant data. Full numbers: `results.json`. Question set and labels: `questions.json`.

## Setup

- **Questions:** 41 (21 for Bid1, 20 for Bid2), each scoped to its own bid.
- **Bids:** Bid1 = Dallas ISD RFP JA-207652 Student and Staff Computing Devices; Bid2 = Maryland State Treasurer PORFP #E20P4600040 Dell Laptops w/Extended Warranty.
- **Ground truth:** Cursor agent (AI assistant), by reading stored chunk text in PostgreSQL. Every relevant chunk carries the quoted evidence that answers the question (grade 2 = states the answer, grade 1 = related context).
- **Retrieval:** top 10 results per query; metrics at k = 1, 3, 5, 10.
- **Index:** `text-embedding-3-small` embeddings (1536 dimensions) in Qdrant; PostgreSQL full-text keyword search; RRF constant 60; chunk size 1000 characters with 150 overlap.
- **Recall@k** counts every labelled chunk, including the text and table copies of the same page, so it is stricter than Hit@k (at least one relevant chunk in the top k).

## Configurations

| Configuration | Semantic | Keyword | LLM rewriting | Synonym expansion |
|---|---|---|---|---|
| keyword_only | no | yes | no | no |
| semantic_only | yes | no | no | no |
| hybrid_rrf | yes | yes | no | no |
| hybrid_rrf_rewriting | yes | yes | yes | no |
| hybrid_rrf_expansion | yes | yes | no | yes |
| hybrid_rrf_reranking | not run: sentence_transformers (cross-encoder runtime) is not installed | | | |
| hybrid_rrf_rewriting_reranking | not run: sentence_transformers (cross-encoder runtime) is not installed | | | |

## Results at k = 1

| Configuration | Recall@k | MRR | Hit@k | nDCG@k | Precision@k | Mean latency (ms) |
|---|---|---|---|---|---|---|
| keyword_only | 0.029 | 0.073 | 0.073 | 0.073 | 0.073 | 27 |
| semantic_only | 0.285 | 0.610 | 0.610 | 0.561 | 0.610 | 469 |
| hybrid_rrf | 0.294 | 0.634 | 0.634 | 0.585 | 0.634 | 514 |
| hybrid_rrf_rewriting | 0.294 | 0.634 | 0.634 | 0.585 | 0.634 | 1958 |
| hybrid_rrf_expansion | 0.282 | 0.610 | 0.610 | 0.561 | 0.610 | 458 |

## Results at k = 3

| Configuration | Recall@k | MRR | Hit@k | nDCG@k | Precision@k | Mean latency (ms) |
|---|---|---|---|---|---|---|
| keyword_only | 0.041 | 0.073 | 0.073 | 0.051 | 0.033 | 27 |
| semantic_only | 0.687 | 0.752 | 0.927 | 0.648 | 0.520 | 469 |
| hybrid_rrf | 0.691 | 0.764 | 0.927 | 0.656 | 0.520 | 514 |
| hybrid_rrf_rewriting | 0.642 | 0.752 | 0.902 | 0.625 | 0.496 | 1958 |
| hybrid_rrf_expansion | 0.663 | 0.744 | 0.902 | 0.638 | 0.504 | 458 |

## Results at k = 5

| Configuration | Recall@k | MRR | Hit@k | nDCG@k | Precision@k | Mean latency (ms) |
|---|---|---|---|---|---|---|
| keyword_only | 0.041 | 0.073 | 0.073 | 0.048 | 0.020 | 27 |
| semantic_only | 0.787 | 0.752 | 0.927 | 0.685 | 0.371 | 469 |
| hybrid_rrf | 0.804 | 0.764 | 0.927 | 0.699 | 0.380 | 514 |
| hybrid_rrf_rewriting | 0.791 | 0.758 | 0.927 | 0.685 | 0.371 | 1958 |
| hybrid_rrf_expansion | 0.767 | 0.744 | 0.902 | 0.673 | 0.366 | 458 |

## Results at k = 10

| Configuration | Recall@k | MRR | Hit@k | nDCG@k | Precision@k | Mean latency (ms) |
|---|---|---|---|---|---|---|
| keyword_only | 0.041 | 0.073 | 0.073 | 0.048 | 0.010 | 27 |
| semantic_only | 0.875 | 0.755 | 0.951 | 0.721 | 0.205 | 469 |
| hybrid_rrf | 0.884 | 0.767 | 0.951 | 0.732 | 0.207 | 514 |
| hybrid_rrf_rewriting | 0.872 | 0.762 | 0.951 | 0.720 | 0.205 | 1958 |
| hybrid_rrf_expansion | 0.872 | 0.751 | 0.951 | 0.716 | 0.205 | 458 |

## First relevant rank per question (top 10; - means not found)

| Question | keyword_only | semantic_only | hybrid_rrf | hybrid_rrf_rewriting | hybrid_rrf_expansion |
|---|---|---|---|---|---|
| Bid1-01: Which organization issued this RFP? | - | - | - | - | - |
| Bid1-02: What was the original solicitation due date? | - | - | - | - | - |
| Bid1-03: What new due date does Addendum 2 set? | - | 1 | 1 | 1 | 1 |
| Bid1-04: How must manual submissions be delivered? | - | 1 | 1 | 1 | 1 |
| Bid1-05: What is the contract term and how many renewals are allowed? | - | 1 | 1 | 1 | 2 |
| Bid1-06: When is the pre-bid conference? | - | 1 | 2 | 3 | 2 |
| Bid1-07: What is the minimum warranty for student Chromebooks? | - | 1 | 1 | 1 | 1 |
| Bid1-08: Is accidental damage protection required for the Chromebooks? | - | 3 | 3 | 3 | 3 |
| Bid1-09: What enrollment is required for Windows devices? | 1 | 2 | 1 | 1 | 1 |
| Bid1-10: How must Chromebooks be enrolled for management? | - | 8 | 8 | 7 | 8 |
| Bid1-11: What is Form 1295 and must it be submitted with the response? | - | 1 | 1 | 1 | 1 |
| Bid1-12: How many evaluation points are given for M/WBE participation? | - | 1 | 1 | 1 | 1 |
| Bid1-13: Does the vendor have to certify Buy America compliance? | - | 1 | 1 | 1 | 1 |
| Bid1-14: What does the RFP ask about hot swap replacement devices? | - | 1 | 1 | 1 | 1 |
| Bid1-15: Which USB port is required on the display monitor? | - | 2 | 2 | 2 | 2 |
| Bid1-16: Do references have to be located in the Dallas/Fort Worth area? | - | 3 | 3 | 3 | 3 |
| Bid1-17: Which cooperative purchasing alliance is Dallas ISD a member of? | 1 | 2 | 1 | 1 | 1 |
| Bid1-18: What services must be included with deliveries and deployments? | - | 1 | 1 | 1 | 1 |
| Bid1-19: Who is the buyer for this RFP and what is the buyer's email? | - | 3 | 3 | 4 | 3 |
| Bid1-20: What are the minimum requirements for the student Windows laptop? | - | 1 | 1 | 1 | 1 |
| Bid1-21: What is the deadline for submitting questions? | - | 3 | 3 | 3 | 1 |
| Bid2-01: What is the PORFP number? | - | 2 | 2 | 2 | 2 |
| Bid2-02: Which organization issued this solicitation? | - | 1 | 1 | 1 | 1 |
| Bid2-03: What is the closing date for proposals? | - | 1 | 1 | 1 | 1 |
| Bid2-04: When are questions due? | - | 1 | 1 | 1 | 2 |
| Bid2-05: What is the delivery requirement after award? | - | 2 | 2 | 2 | 1 |
| Bid2-06: How long is the extended warranty on the laptops? | - | 1 | 1 | 1 | 1 |
| Bid2-07: Which laptop model is requested and in what quantity? | - | 1 | 1 | 1 | 1 |
| Bid2-08: Which docking station is requested? | - | 1 | 1 | 1 | 1 |
| Bid2-09: What processor does the quoted Latitude 5550 have? | - | 1 | 1 | 1 | 1 |
| Bid2-10: How much memory does the quoted laptop configuration include? | - | 2 | 2 | 2 | 2 |
| Bid2-11: Is a Mercury Affidavit required? | - | 1 | 1 | 1 | 1 |
| Bid2-12: When must invoices be submitted? | - | 1 | 1 | 1 | 2 |
| Bid2-13: Where should invoices be emailed? | - | 2 | 2 | 2 | 6 |
| Bid2-14: What is the agency point of contact's phone number? | - | 2 | 2 | 2 | 2 |
| Bid2-15: How must proposals be submitted? | - | 1 | 1 | 1 | 1 |
| Bid2-16: What software readiness is required for the laptops? | - | 1 | 1 | 1 | 1 |
| Bid2-17: What are the evaluation criteria for award? | - | 1 | 1 | 1 | 1 |
| Bid2-18: Who is the on-site delivery contact? | 1 | 1 | 1 | 1 | 1 |
| Bid2-19: What must be presented upon award for the warranty? | - | 1 | 1 | 1 | 1 |
| Bid2-20: What is the PORFP type? | - | 2 | 2 | 2 | 2 |

## Findings

Written after reading the measured results above and the chunks each configuration returned (`results.json` → `retrieved`).

1. **Hybrid RRF is the best configuration measured, but only slightly ahead of semantic only.** At k = 5, hybrid RRF reaches Recall 0.804 / MRR 0.764 / Hit 0.927, against 0.787 / 0.752 / 0.927 for semantic only. On most questions both put the first relevant chunk at the same rank. Hybrid wins only where keyword search found an exact phrase (Bid1-09 Windows enrollment and Bid1-17 cooperative purchasing alliance, rank 2 → 1).
2. **Keyword search alone almost never returns anything for natural-language questions** (0.17 results per question on average; Hit@5 0.073). `KeywordSearch` uses PostgreSQL `websearch_to_tsquery` with the `simple` configuration, which requires every word of the question, including words like "what", "is" and "the", to appear in one chunk, with no stemming. It returned anything for only 4 of 41 questions, and a relevant chunk for 3 of them (Bid1-09, Bid1-17, Bid2-18). It behaves as an exact-wording matcher, not as a recall source. Making it match any term (OR semantics), or using an English configuration that drops stop words, is the obvious next experiment. That change has not been made or measured.
3. **LLM query rewriting did not help on this set.** Recall@5 was 0.791 vs 0.804 and MRR 0.758 vs 0.764, and mean latency rose from roughly 0.5 s to roughly 2 s per query (one extra LLM call, then retrieval for every variant). The questions are already specific, so the extra variants mostly add near-duplicate candidates. The rewrites come from an LLM, so this row varies slightly between runs (MRR was 0.756 on an earlier run of the same script). The live `.env` has `QUERY_REWRITING_ENABLED=true`. On this evidence, turning it off for the search API would be cheaper with no loss of quality, but that decision is the user's.
4. **The chat's synonym expansion (`expand_query`) slightly hurt ranking** (MRR 0.744; Hit@5 0.902). Appending synonyms pushed a relevant chunk down for Bid2-13 (rank 2 → 6) and Bid1-05, while helping Bid1-21 (rank 3 → 1).
5. **Questions every configuration missed in the top 10:**
   - **Bid1-01, "Which organization issued this RFP?":** the top results are page headers containing "Dallas ISD", but no sentence stating who issued the RFP. The labels only count chunks that state the issuer explicitly (the BidNet "Issuing Organization" line and the scope paragraph). This miss is therefore partly due to the strict labelling.
   - **Bid1-02, "What was the original solicitation due date?":** retrieval returned the extended date (BidNet closing date, Addendum 2) instead of the page-2 timeline row with the original due date of 27-Jun-2024. This is a genuine miss: the question is about an earlier value that the corpus has since overridden.
6. **Recall@k is lower than Hit@k by design.** The ingestion stores many pages as both a text chunk and a table chunk, and both are labelled relevant. A top-k list that finds the answer once still misses the duplicate.
7. **Reranking was not measured.** The cross-encoder runtime (`sentence_transformers`) is not installed in this environment, so the reranking configurations are listed as "not run" and have no numbers.

**Limits of this evaluation:**

- There are 41 questions over two bids, labelled by the Cursor agent (an AI assistant) from the stored chunk text, not by a human domain expert. `questions.json` quotes the evidence for every relevant chunk, and `backend/evaluation/retrieval_label_spec.json` holds the answer patterns used to find those chunks, so each label can be checked.
- The scores reflect this corpus only. Differences of about 0.01 are within run-to-run noise for the rewriting configuration.
