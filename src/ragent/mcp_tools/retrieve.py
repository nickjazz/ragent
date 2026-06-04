"""MCP tool spec for first-party retrieval."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fastapi.concurrency import run_in_threadpool

from ragent.mcp_tools.registry import McpToolSpec
from ragent.pipelines.retrieve import (
    build_es_filters,
    dedupe_by_document,
    doc_to_source_entry,
    run_retrieval,
)
from ragent.schemas.retrieve import RetrieveRequest

_SOURCE_LABEL = "資料來源"


def build_retrieve_tool(
    retrieval_pipeline: Any,
    *,
    excerpt_max_chars: int,
    run_retrieval_fn: Callable[..., list[Any]] = run_retrieval,
) -> McpToolSpec:
    async def _handler(model: Any) -> dict[str, Any]:
        request = cast(RetrieveRequest, model)
        docs = await run_in_threadpool(
            run_retrieval_fn,
            retrieval_pipeline,
            query=request.query,
            filters=build_es_filters(request.source_app, request.source_meta),
            top_k=request.top_k,
            min_score=request.min_score,
        )
        if request.dedupe:
            docs = dedupe_by_document(docs)
        entries = [doc_to_source_entry(d, max_chars=excerpt_max_chars) for d in docs]
        return {
            "content": [{"type": "text", "text": render_chunks(entries)}],
            "isError": False,
        }

    return McpToolSpec(
        name="retrieve",
        description=(
            "Retrieve relevant document chunks for a query. "
            "Returns ranked excerpts without LLM synthesis."
        ),
        request_model=RetrieveRequest,
        handler=_handler,
        annotations={"readOnlyHint": True},
    )


def header_field(value: str | None) -> str:
    """Strip CR and LF so metadata stays on one header line."""
    return (value or "").replace("\n", " ").replace("\r", "")


def render_chunks(entries: list[dict]) -> str:
    """Format retrieve entries as numbered evidence blocks for MCP callers."""
    if not entries:
        return "Found 0 chunk(s)."
    parts = [f"Found {len(entries)} chunk(s).\n"]
    for i, entry in enumerate(entries, start=1):
        score = entry.get("score")
        source_app = header_field(entry.get("source_app"))
        doc_id = header_field(entry.get("document_id"))
        title = header_field(entry.get("source_title"))
        header = f"[{_SOURCE_LABEL} #{i}]"
        if score is not None:
            header += f" score={score:.2f}"
        if source_app:
            header += f" | source_app={source_app}"
        if doc_id:
            header += f" | document_id={doc_id}"
        if title:
            header += f" | title={title}"
        excerpt = entry.get("excerpt") or ""
        parts.append(f"{header}\n{excerpt}\n---")
    return "\n".join(parts)
