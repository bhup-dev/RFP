"""Render a workflow trace written by ``python -m app.cli trace`` as a Markdown timeline.

Usage, from the backend directory:
    python -m scripts.format_trace TRACE.json [--output TRACE.md] [--label "Bid1 (...)"]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def cell(value, limit: int = 140) -> str:
    if value is None or value == "":
        return "—"
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    text = " ".join(value.split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def where(citation: dict) -> str:
    page = citation.get("page_number")
    name = citation.get("file_name") or "unknown file"
    return f"{name}" + (f" p.{page}" if page else "")


def citations_cell(citations: list[dict]) -> str:
    if not citations:
        return "none"
    first = where(citations[0])
    more = f" (+{len(citations) - 1} more)" if len(citations) > 1 else ""
    return cell(first + more, 110)


def seconds(start: str | None, end: str | None) -> str:
    if not start or not end:
        return "—"
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return f"{delta.total_seconds():.1f}"


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(row) + " |" for row in rows),
    ]


def render(trace: dict, label: str | None = None) -> str:
    workflow = trace["workflow"]
    lines = [
        f"# Agent trace: {label} workflow run" if label else "# Agent trace: bid workflow run",
        "",
        f"> {trace['note']}",
        "",
        "## Run",
        "",
        *([f"- **Bid:** {label}"] if label else []),
        f"- **Workflow id:** `{workflow['workflow_id']}` (version {workflow['workflow_version']})",
        f"- **Bid id:** `{workflow['bid_id']}`",
        f"- **Status:** {workflow['status']}; human review required: {workflow['requires_human_review']}",
        (f"- **Started / completed:** {workflow['started_at']} → {workflow['completed_at']} "
        f"({seconds(workflow['started_at'], workflow['completed_at'])} s)"),
        f"- **Documents:** {workflow['eligible_document_count']} eligible",
        f"- **Profile id:** `{trace.get('profile_id')}`",
    ]
    for warning in workflow.get("warnings") or []:
        lines.append(f"- **Warning:** {cell(warning, 300)}")
    for error in workflow.get("errors") or []:
        lines.append(f"- **Error:** {cell(error, 300)}")

    lines += ["", "## Timeline (LangGraph nodes, in execution order)", ""]
    rows = []
    for number, step in enumerate(trace["steps"], 1):
        references = ", ".join(
            f"{name}={value[:8]}…" for name, value in (step.get("run_references") or {}).items() if value
        )
        rows.append(
            [
                str(number),
                f"`{step['node']}`",
                step["status"],
                f"{step['duration_ms'] / 1000:.1f}" if step.get("duration_ms") is not None else "—",
                "yes" if step["calls_model"] else "no",
                cell(step.get("prompt_location"), 90),
                cell(references, 120),
            ]
        )
    lines += table(["#", "Node", "Status", "Seconds", "Calls model", "Prompt defined in", "Run records written"], rows)
    lines += [
        "",
        ("Nodes do not send messages to each other. Each node writes its run to PostgreSQL and passes the run id "
        "through the LangGraph state; later nodes load earlier runs by those ids."),
    ]

    extraction = trace.get("extraction")
    if extraction:
        lines += [
            "",
            "## 1. Field extraction",
            "",
            (f"Run `{extraction['extraction_run_id']}`, model `{extraction['model_name']}`, status "
            f"{extraction['status']}, {seconds(extraction['started_at'], extraction['completed_at'])} s, "
            f"{len(extraction['document_ids'])} documents."),
            "",
        ]
        lines += table(
            ["Field", "Value", "Confidence", "Status", "Citations"],
            [
                [
                    field["field_name"],
                    cell(field.get("value"), 120),
                    cell(field.get("confidence")),
                    cell(field.get("validation_status")),
                    citations_cell(field.get("citations") or []),
                ]
                for field in extraction["fields"]
            ],
        )

    addendum = trace.get("addendum_analysis")
    if addendum:
        lines += [
            "",
            "## 2. Addendum analysis",
            "",
            (f"Run `{addendum['addendum_analysis_run_id']}`, model `{addendum['model_name']}`, status "
            f"{addendum['status']}; {len(addendum['original_document_ids'])} original and "
            f"{len(addendum['addendum_document_ids'])} addendum documents; ordering uncertain: "
            f"{addendum['ordering_uncertain']}."),
            "",
        ]
        lines += table(
            ["Field", "Change", "Original", "Amended", "Severity", "Confidence"],
            [
                [
                    cell(item.get("field_name")),
                    cell(item.get("status")),
                    cell(item.get("original_value"), 80),
                    cell(item.get("amended_value"), 80),
                    cell(item.get("severity")),
                    cell(item.get("confidence")),
                ]
                for item in addendum["findings"]
            ],
        )

    agents = trace.get("specialized_agents") or []
    if agents:
        lines += ["", "## 3. Specialized agents", ""]
        for agent in agents:
            lines += [
                f"### {agent['agent_name']}",
                "",
                (f"Run `{agent['agent_run_id']}`, model `{agent['model_name']}`, status {agent['status']}, "
                f"{agent['evidence_chunk_count']} evidence chunks, {len(agent['findings'])} findings, "
                f"{seconds(agent['started_at'], agent['completed_at'])} s."),
                "",
            ]
            for heading, items in (("Warnings", agent["warnings"]), ("Errors", agent["errors"]), ("Review issues", agent["review_issues"])):
                if items:
                    lines += [f"{heading}: {cell(items, 400)}", ""]
            if agent["findings"]:
                lines += table(
                    ["Finding", "Classification", "Confidence", "Citations"],
                    [
                        [
                            cell(f"{finding['title']}: {finding['description']}", 160),
                            cell(finding.get("classification")),
                            cell(finding.get("confidence")),
                            citations_cell(finding.get("citations") or []),
                        ]
                        for finding in agent["findings"]
                    ],
                )
                lines.append("")

    validation = trace.get("validation")
    if validation:
        thresholds = validation.get("thresholds") or {}
        lines += [
            "## 4. Validation",
            "",
            (f"Run `{validation['validation_run_id']}`, model `{validation['model_name']}`, status "
            f"{validation['status']}, overall {validation['overall_status']}; thresholds high ≥ "
            f"{thresholds.get('high')}, medium ≥ {thresholds.get('medium')}; addendum check "
            f"{validation['addendum_check_status']}."),
            "",
        ]
        lines += table(
            ["Field", "Status", "Confidence", "Category", "Reasons"],
            [
                [
                    field["field_name"],
                    cell(field.get("status")),
                    cell(field.get("confidence_score")),
                    cell(field.get("confidence_category")),
                    cell("; ".join(field.get("reasons") or []), 160),
                ]
                for field in validation["fields"]
            ],
        )

    review_items = trace.get("review_items") or []
    lines += ["", "## 5. Review items created", ""]
    if review_items:
        lines += table(
            ["Field", "Stage", "Severity", "Status", "Proposed value", "Reason"],
            [
                [
                    cell(item.get("field_name")),
                    cell(item.get("source_stage")),
                    cell(item.get("severity")),
                    cell(item.get("status")),
                    cell(item.get("proposed_value"), 80),
                    cell(item.get("reason"), 140),
                ]
                for item in review_items
            ],
        )
    else:
        lines.append("None.")
    lines += [
        "",
        "## 6. Profile",
        "",
        (f"`generate_profile` built profile `{trace.get('profile_id')}` from the runs above; `finalize` recorded "
        f"workflow status {workflow['status']}."),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label", help='bid name shown in the title, e.g. "Bid1 (Dallas ISD JA-207652)"')
    args = parser.parse_args()
    output = args.output or args.trace.with_suffix(".md")
    output.write_text(render(json.loads(args.trace.read_text()), args.label))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
