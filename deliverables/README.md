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

| Bid | Documents | Workflow status | Fields with a value | Validated / flagged for review / not found |
|---|---|---|---|---|
| **Bid1** | Dallas ISD Purchasing Department, Request For Proposal 168884, **JA-207652 Student and Staff Computing Devices** (RFP plus Addenda 1 and 2 and the BidNet page) | needs_review | 15 of 20 | 13 / 2 / 5 |
| **Bid2** | Maryland State Treasurer PORFP **#E20P4600040**, Dell Latitude 5550 laptops with extended warranty and WD22TB4 docks | needs_review | 16 of 20 | 15 / 1 / 4 |

"Flagged for review" counts fields the validator marked conflicting, partially supported, or unsupported. "Not found" fields have no value because the retrieved evidence did not state one; for Bid1, the bid bond, delivery date, manufacturer registration, and model and part numbers are genuinely absent from the RFP (vendors propose the devices).

An earlier run of the same bids, before the extraction and validation changes described in the root README, had 9 and 13 fields with values and only 2 and 5 validated fields.

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

- **Wrong Bid1 payment terms, validated anyway.** Bid1 "Payment Terms" is the M/WBE clause saying prime vendors must pay their subcontractors within 10 days of being paid by the district. That is not the district's payment terms, which the RFP does not state. Both the extraction and validator prompts say so explicitly, but gpt-4o-mini still extracted and validated it. The same rule did work for Bid2, where invoicing instructions were marked `unsupported`.
- **Bid1 due date flagged as conflicting.** The extracted value is `07/09/2024 03:00 PM EDT` (BidNet page); Addendum 2 states 09-JUL-2024 14:00 CST. These are probably the same moment in two timezones, but the validator could not confirm it and flagged it for review, which is the intended behaviour.
- **Bid2 model and part numbers overlap.** The source's "Model #" column gives `SI# CC7802` for the laptop and `WD22TB4` for the dock, and the same values were also reported as part numbers. No separate manufacturer part numbers are listed in the PORFP.
- **Item-level metadata inside list values.** Some list values (for example Additional Documentation Required) carry per-item `chunk_id`, `status` or `notes` keys written by the model. Field-level status and citations are still authoritative.
- **Rejected agent findings.** The Bid1 run warns that two domain-agent findings were rejected as malformed. They were dropped, not stored.

## Highlights

- **Retrieval:** 41 questions (21 for Bid1, 20 for Bid2), labelled from the stored chunk text by the Cursor agent (an AI assistant), not by a human expert. Hybrid RRF scored Recall@5 0.804 and MRR 0.764. Keyword search alone scored very low, and LLM query rewriting did not help. Reranking was not run because `sentence-transformers` is not installed. Details and per-question ranks are in the report.
- **Q&A:** 71 questions, with Bid1 as the main bid and Bid2 as the comparison bid. They include follow-up conversations, Bid1-versus-Bid2 comparisons and adversarial prompts. 54 answers came with citations; 17 were declined as not supported by the documents (`found: false`). Some declined answers still cite the stored records that show a field is missing.
- **Trace:** a full 13-node run for Bid1 (123 s). It covers 20 extracted fields, 3 addendum changes (including the due date moving from 27 June to 9 July 2024), findings from the four domain agents (9, 3, 6 and 4) plus 0 from the deterministic evidence-review agent, 20 validation results and 2 review items.
