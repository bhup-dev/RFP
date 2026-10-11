"""Render the JSON written by ``scripts.chat_eval`` as a readable Markdown Q&A log.

Usage, from the backend directory:
    python -m scripts.format_qa_log QA_LOG.json --primary-label "Bid1 ..." --secondary-label "Bid2 ..." \
        [--output QA_LOG.md]

Answers, sources and warnings are copied verbatim from the JSON; nothing is scored or rewritten.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def one_line(text: str) -> str:
    return " ".join(str(text).split())


def render(rows: list[dict], labels: dict[str, str], source: str) -> str:
    answered = [row for row in rows if "error" not in row]
    found = sum(1 for row in answered if row["found"])
    lines = [
        "# Sample Q&A log",
        "",
        (f"Generated from `{source}`, written by `backend/scripts/chat_eval.py` against the live chat "
        "service (`POST /api/v1/bids/{bid_id}/ask` code path). The chat is read-only, so questions that "
        "ask it to approve, reject or re-run something do not change stored data."),
        "",
        f"- **Questions:** {len(rows)} ({len(answered)} answered, {len(rows) - len(answered)} errors)",
        (f"- **Answers with supporting citations (`found: true`):** {found}; "
        f"**declined as unsupported (`found: false`):** {len(answered) - found}"),
        f"- **Primary bid:** {labels['primary']}",
        f"- **Secondary bid:** {labels['secondary']}",
        ("- **Citation markers** in answers refer to the sources listed under each answer: "
        "`S` = document passage, `R` = stored analysis record, `P` = fixed policy note written in code."),
        "- Questions sharing a conversation letter (B, D, G) were asked in order with the previous turns as history.",
        "",
        "## Index",
        "",
        "| # | Bid | Question | Found | Sources |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        question = one_line(row["question"])
        short = question if len(question) <= 90 else question[:89] + "…"
        if "error" in row:
            lines.append(f"| [{row['id']}](#q-{row['id'].lower()}) | — | {short} | error | — |")
            continue
        bid = labels[row["bid"]].split(" ")[0] + (" + compare" if row["compare"] else "")
        lines.append(
            f"| [{row['id']}](#q-{row['id'].lower()}) | {bid} | {short.replace('|', '/')} | "
            f"{'yes' if row['found'] else 'no'} | {len(row['sources'])} |"
        )
    for row in rows:
        lines += ["", f"## Q {row['id']}", ""]
        if "error" in row:
            lines += [f"**Question:** {row['question']}", "", f"**Error:** `{row['error']}`"]
            continue
        bid = labels[row["bid"]] + (f", compared with {labels['secondary' if row['bid'] == 'primary' else 'primary']}" if row["compare"] else "")
        lines += [f"**Bid:** {bid}", "", "**Question:**", ""]
        lines += ["> " + line for line in row["question"].splitlines()]
        lines += ["", f"**Answer** (found: {str(row['found']).lower()}):", "", row["answer"].strip(), ""]
        if row["warnings"]:
            lines += ["**Warnings:**", ""] + [f"- {one_line(warning)}" for warning in row["warnings"]] + [""]
        if row["sources"]:
            lines += ["**Sources:**", "", "| Id | Kind | Bid | File | Page | Excerpt |", "|---|---|---|---|---|---|"]
            for item in row["sources"]:
                excerpt = one_line(item.get("excerpt") or "").replace("|", "/")
                lines.append(
                    f"| {item['id']} | {item['kind']} | {item.get('bid') or '—'} | "
                    f"{(item.get('file') or '—').replace('|', '/')} | {item.get('page') or '—'} | {excerpt} |"
                )
        else:
            lines.append("**Sources:** none")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path)
    parser.add_argument("--primary-label", required=True)
    parser.add_argument("--secondary-label", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.log.with_suffix(".md")
    labels = {"primary": args.primary_label, "secondary": args.secondary_label}
    output.write_text(render(json.loads(args.log.read_text()), labels, args.log.name))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
