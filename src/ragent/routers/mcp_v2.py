"""MCP server router — `POST /mcp/v2` JSON-RPC 2.0 (spec §3.8).

Sole tool: the document-scoped `retrieve` (wraps POST /retrieve/v2). v1's
corpus-wide retrieve and create_skill are deliberately NOT carried over —
this surface exists for chat agents whose retrieval must stay inside the
caller-owned documents listed in the `<attachments>` block. The JSON-RPC
transport lives in routers/mcp_transport.py (shared with /mcp/v1).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from jsonschema import Draft7Validator

from ragent.errors.codes import HttpErrorCode
from ragent.pipelines.retrieve import (
    DEFAULT_TOP_K,
    EXCERPT_MAX_CHARS_DEFAULT,
    build_document_id_filter,
    doc_to_source_entry,
    run_retrieval,
)
from ragent.routers.mcp_tools.context_render import render_context_markdown
from ragent.routers.mcp_tools.retrieve_documents import RETRIEVE_DOCUMENTS_TOOL
from ragent.routers.mcp_transport import (
    INVALID_PARAMS,
    TOOL_EXECUTION_FAILED,
    McpToolError,
    create_jsonrpc_router,
    validate_against,
)
from ragent.services.retrieve_v2_service import DocumentForbidden, RetrieveV2Service

logger = structlog.get_logger(__name__)

_INPUT_VALIDATOR = Draft7Validator(RETRIEVE_DOCUMENTS_TOOL.inputSchema)


def create_mcp_v2_router(
    retrieval_pipeline: Any,
    retrieve_v2_service: RetrieveV2Service,
    *,
    excerpt_max_chars: int = EXCERPT_MAX_CHARS_DEFAULT,
) -> APIRouter:
    async def _run_retrieve_documents(
        arguments: dict[str, Any], user_id: str | None
    ) -> dict[str, Any]:
        validate_against(_INPUT_VALIDATOR, arguments)
        # Zero-trust gate BEFORE any ES access: unauthenticated callers and
        # foreign/unknown ids fail identically (no existence oracle).
        try:
            await retrieve_v2_service.assert_owner(user_id, arguments["document_id_list"])
        except DocumentForbidden as exc:
            raise McpToolError(
                INVALID_PARAMS,
                HttpErrorCode.DOCUMENT_FORBIDDEN.value,
                "one or more document ids are not accessible",
            ) from exc
        try:
            docs = await run_in_threadpool(
                run_retrieval,
                retrieval_pipeline,
                query=arguments["query"],
                filters=build_document_id_filter(arguments["document_id_list"]),
                top_k=arguments.get("top_k", DEFAULT_TOP_K),
                min_score=arguments.get("min_score"),
            )
        except Exception as exc:
            logger.exception("mcp.tool.error", tool="retrieve_v2", error_type=type(exc).__name__)
            raise McpToolError(
                TOOL_EXECUTION_FAILED,
                HttpErrorCode.MCP_TOOL_EXECUTION_FAILED.value,
                str(exc) or "tool execution failed",
            ) from exc
        entries = [doc_to_source_entry(d, max_chars=excerpt_max_chars) for d in docs]
        # Dual channel: sources JSON for the UI, markdown digest for the LLM.
        return {
            "content": [{"type": "text", "text": render_context_markdown(entries)}],
            "structuredContent": {"sources": entries},
            "isError": False,
        }

    return create_jsonrpc_router(
        "/mcp/v2", [RETRIEVE_DOCUMENTS_TOOL], {"retrieve": _run_retrieve_documents}
    )
