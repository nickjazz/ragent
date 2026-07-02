"""Shared <context>-markdown rendering for MCP retrieve tools (v1 + v2).

Moved verbatim from routers/mcp.py so both protocol versions render the same
dual-channel digest (markdown for the LLM, structuredContent for the UI).
"""

from __future__ import annotations

import re

# Corpus text containing literal <context>/</context> tags must not close the
# wrapper that hosts use to isolate tool context (PR #171 codex review).
_CONTEXT_TAG_RE = re.compile(r"<(/?context)>", re.IGNORECASE)


def _neutralize_context_tags(value: str) -> str:
    return _CONTEXT_TAG_RE.sub(r"&lt;\1&gt;", value)


def _header_field(value: str | None) -> str:
    """Sanitise a metadata value for a single markdown line: CR/LF stripped,
    embedded <context> tags neutralised."""
    return _neutralize_context_tags((value or "").replace("\n", " ").replace("\r", ""))


def _md_cell(value: str | None) -> str:
    """Sanitise a value for a markdown table cell: single line, `|` escaped
    so a malicious title cannot break the table or inject rows."""
    return _header_field(value).replace("|", "\\|")


def _safe_link_url(value: str | None) -> str:
    """Return a linkifiable URL or "" (render plain title instead).

    Only http(s) destinations are linkified — a crafted javascript: URL must
    not become a clickable link in user-presentable markdown. Characters that
    terminate a markdown link destination or split a table cell are
    percent-encoded; the raw URL stays in structuredContent.
    """
    # Sanitise before encoding — a CR/LF must become %20, not a raw space.
    url = _header_field(value).strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    for char, encoded in (("(", "%28"), (")", "%29"), (" ", "%20"), ("|", "%7C")):
        url = url.replace(char, encoded)
    return url


def render_context_markdown(entries: list[dict]) -> str:
    """Render retrieve entries as a <context>-wrapped markdown digest.

    Layout: a user-presentable citation table (#, 資料來源, 來源系統 — no
    internal fields like document_id/score, those live in structuredContent),
    then one `### [N]` blockquoted excerpt section per source for LLM
    grounding. No natural-language wording, so calling LLMs treat the block
    as injected context data rather than prose to transcribe.
    """
    if not entries:
        return "<context>\n</context>"
    rows = ["| # | 資料來源 | 來源系統 |", "|---|---------|---------|"]
    excerpt_blocks = []
    for i, entry in enumerate(entries, start=1):
        # Pipe-escaping is a table-cell concern only — headings keep the raw `|`.
        title = _header_field(entry.get("source_title")) or "(未命名)"
        cell_title = title.replace("|", "\\|")
        url = _safe_link_url(entry.get("source_url"))
        link = f"[{cell_title}]({url})" if url else cell_title
        rows.append(f"| {i} | {link} | {_md_cell(entry.get('source_app'))} |")
        excerpt = _neutralize_context_tags(entry.get("excerpt") or "")
        quoted = "\n".join(f"> {line}" for line in excerpt.splitlines() or [""])
        excerpt_blocks.append(f"### [{i}] {title}\n{quoted}")
    body = "\n".join(rows) + "\n\n" + "\n\n".join(excerpt_blocks)
    return f"<context>\n{body}\n</context>"
