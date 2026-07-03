"""Joiner utilities: ES filter builders, deduplication, and source-entry conversion."""

from __future__ import annotations

from typing import Any

from ragent.pipelines.retrieve._constants import EXCERPT_MAX_CHARS_DEFAULT
from ragent.schemas.attachments import ATTACHMENT_SOURCE_APP as _ATTACHMENT_SOURCE_APP

_HAYSTACK_JOIN_MODE = {"rrf": "reciprocal_rank_fusion", "concatenate": "concatenate"}


def combine_filters(base: dict | None, extra: dict) -> dict:
    """AND-combine an optional base filter with an additional clause."""
    if base is None:
        return extra
    return {"operator": "AND", "conditions": [base, extra]}


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


def build_attachment_exclusion_filter() -> dict:
    """Exclude chat_attachment chunks from corpus-wide queries (/retrieve/v1, /chat).

    Attachment documents are personal files scoped to a session owner; they
    must be retrieved via /retrieve/v2 (anti-IDOR gate), never surfaced through
    the shared corpus.
    """
    return {"field": "source_app", "operator": "!=", "value": _ATTACHMENT_SOURCE_APP}


def build_document_id_filter(document_ids: list[str]) -> dict:
    """Haystack filter restricting retrieval to an explicit document set.

    The `in` operator compiles to an ES `terms` clause inside the retriever's
    bool.filter context — the isolation guarantee of /retrieve/v2.
    """
    # verified against haystack-elasticsearch (see test_retrieve_v2's
    # _normalize_filters assertion pinning the compiled ES query shape).
    return {"field": "document_id", "operator": "in", "value": list(document_ids)}


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
