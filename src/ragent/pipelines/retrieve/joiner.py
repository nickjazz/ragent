"""Joiner utilities: ES filter builders, deduplication, and source-entry conversion."""

from __future__ import annotations

from typing import Any

from ragent.pipelines.retrieve._constants import EXCERPT_MAX_CHARS_DEFAULT

_HAYSTACK_JOIN_MODE = {"rrf": "reciprocal_rank_fusion", "concatenate": "concatenate"}


def build_es_filters(source_app: str | None, source_meta: str | None) -> dict | None:
    clauses = []
    if source_app:
        clauses.append({"field": "source_app", "operator": "==", "value": source_app})
    if source_meta:
        clauses.append({"field": "source_meta", "operator": "==", "value": source_meta})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"operator": "AND", "conditions": clauses}


def dedupe_by_document(docs: list[Any]) -> list[Any]:
    """Keep one chunk per `document_id`, preserving order; chunks without a
    `document_id` are passed through unchanged."""
    seen: set[str] = set()
    out = []
    for doc in docs:
        doc_id = (doc.meta or {}).get("document_id")
        if doc_id not in seen:
            if doc_id is not None:
                seen.add(doc_id)
            out.append(doc)
    return out


def doc_to_source_entry(doc: Any, *, max_chars: int = EXCERPT_MAX_CHARS_DEFAULT) -> dict:
    meta = doc.meta or {}
    excerpt_src = meta.get("raw_content") or (doc.content or "")
    return {
        "document_id": meta.get("document_id"),
        "source_app": meta.get("source_app"),
        "source_id": meta.get("source_id"),
        "source_meta": meta.get("source_meta"),
        "type": "knowledge",
        "source_title": meta.get("source_title"),
        "source_url": meta.get("source_url"),
        "mime_type": meta.get("mime_type"),
        "excerpt": excerpt_src[:max_chars],
        "score": doc.score if hasattr(doc, "score") else None,
    }
