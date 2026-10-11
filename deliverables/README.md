# Deliverables

Every file here was produced by running the code in this repository against the locally processed bids. Nothing was hand-edited except `architecture.md` and `retrieval_evaluation/findings.md`, which are written analysis. Bid documents themselves are not committed (`backend/data/` is gitignored).

| Deliverable | Files | Produced by |
|---|---|---|
| Source code (ingestion, search engine, agents, API, CLI) | `backend/`, `frontend/` | — |
| README with setup, run modes and design decisions | [`../README.md`](../README.md) | written |
| Architecture diagram | [`architecture.md`](architecture.md) | written, checked against the code |
| JSON output per bid folder | [`bid_outputs/Bid1.json`](bid_outputs/Bid1.json), [`bid_outputs/Bid2.json`](bid_outputs/Bid2.json) | `python -m scripts.export_bid_outputs … --names Bid1 Bid2` |
| Retrieval evaluation | [`retrieval_evaluation/report.md`](retrieval_evaluation/report.md), `questions.json`, `results.json` | `python -m scripts.retrieval_eval` |
| Sample Q&A log | [`qa_log/qa_log.md`](qa_log/qa_log.md), `qa_log.json` | `python -m scripts.chat_eval`, then `scripts.format_qa_log` |
| Agent trace of one extraction run | [`agent_trace/Bid1_extraction_trace.md`](agent_trace/Bid1_extraction_trace.md), `.json` | `python -m app.cli trace`, then `scripts.format_trace` |
| Demo video | not included yet | — |

## Bids covered

| Bid | Documents | Workflow status | Fields with a value | Validated / needs review / not found |
|---|---|---|---|---|
| **Bid1** | Dallas ISD Purchasing Department, Request For Proposal 168884, **JA-207652 Student and Staff Computing Devices** (RFP plus Addenda 1 and 2 and the BidNet page) | needs_review | 9 of 20 | 2 / 7 / 11 |
| **Bid2** | Maryland State Treasurer PORFP **#E20P4600040**, Dell Latitude 5550 laptops with extended warranty and WD22TB4 docks | needs_review | 13 of 20 | 5 / 8 / 7 |

**Bid ids.** `metadata.bid_identifier` in each JSON file is the bid's internal name (`Bid1`, `Bid2`). It is a grouping name, not the solicitation number; the solicitation number is the "Bid Number" field. The database ids are:

- Bid1: `e5cee25c-acb3-494f-8879-e2cd6a84de4f`
- Bid2: `e2d10a26-8e75-4acc-91eb-1694912289e9`

On this machine the source files are stored in the folders `backend/data/bids/bid6` (Bid1) and `bid5` (Bid2). Citations name only the file, not the folder.

Each bid JSON is the stored intelligence profile. For each of the 20 fields it gives:

- the value
- the validation status
- a heuristic confidence score
- supporting and contradicting citations (file, page, chunk id, excerpt)
- addendum changes and a review recommendation

## Known issues in these outputs

These are reported as found, not corrected:

- **Wrong Bid2 delivery date.** Bid2 "Delivery Date" is stored as `06/10/2024` with status `validated` and confidence 1.0, but the source says delivery is within 45 days of award. The validator accepted it because the date string appears in the cited table. This shows the limit of literal-match validation.
- **Bid1 validation was weak in this run.** The semantic check failed for all 7 Bid1 fields that it was asked to verify, so they are `needs_review` with confidence 0.38 to 0.54 and became the 7 review items in the trace.
- **A mismatched Bid1 addendum finding.** One Bid1 finding pairs "white glove services" (original) with the addendum's statement about accidental damage protection (amended). The comparison model matched these two statements wrongly.

## Highlights

- **Retrieval:** 41 questions (21 for Bid1, 20 for Bid2), labelled from the stored chunk text by the Cursor agent (an AI assistant), not by a human expert. Hybrid RRF scored Recall@5 0.804 and MRR 0.764. Keyword search alone scored very low, and LLM query rewriting did not help. Reranking was not run because `sentence-transformers` is not installed. Details and per-question ranks are in the report.
- **Q&A:** 71 questions, with Bid1 as the main bid and Bid2 as the comparison bid. They include follow-up conversations, Bid1-versus-Bid2 comparisons and adversarial prompts. 51 answers came with citations; 20 were declined as not supported by the documents (`found: false`). Some declined answers still cite the stored records that show a field is missing.
- **Trace:** a full 13-node run for Bid1 (96 s). It covers 20 extracted fields, 6 addendum changes, findings from the four domain agents (10, 3, 8 and 4) plus 0 from the deterministic evidence-review agent, 20 validation results and 7 review items.
