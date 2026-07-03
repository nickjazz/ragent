"""MCP server router — `POST /mcp/v1` JSON-RPC 2.0 (§3.8, B47).

Methods: initialize / notifications/initialized / tools/list / tools/call / ping.
Tools: `retrieve` (document-scoped, Anti-IDOR via document_id_list) and, when a
skill service is wired, `create_skill`. The JSON-RPC transport lives in
routers/mcp_transport.py.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from jsonschema import Draft7Validator
from mcp.types import Tool

from ragent.errors.codes import HttpErrorCode
from ragent.pipelines.retrieve import (
    DEFAULT_MIN_SCORE,
    DEFAULT_TOP_K,
    EXCERPT_MAX_CHARS_DEFAULT,
    build_document_id_filter,
    doc_to_source_entry,
    run_retrieval,
)
from ragent.routers.mcp_tools.context_render import render_context_markdown
from ragent.routers.mcp_tools.create_skill import CREATE_SKILL_TOOL
from ragent.routers.mcp_tools.retrieve_documents import RETRIEVE_DOCUMENTS_TOOL
from ragent.routers.mcp_transport import (
    INVALID_PARAMS,
    TOOL_EXECUTION_FAILED,
    TOOL_FORBIDDEN,
    McpToolError,
    ToolHandler,
    create_jsonrpc_router,
    validate_against,
)
from ragent.services.retrieve_v2_service import DocumentForbidden, RetrieveV2Service
from ragent.services.skill_service import SkillNameConflictError

logger = structlog.get_logger(__name__)

_INPUT_VALIDATOR = Draft7Validator(RETRIEVE_DOCUMENTS_TOOL.inputSchema)
_CREATE_SKILL_INPUT_VALIDATOR = Draft7Validator(CREATE_SKILL_TOOL.inputSchema)


def create_mcp_router(
    retrieval_pipeline: Any,
    retrieve_v2_service: RetrieveV2Service,
    *,
    skill_service: Any | None = None,
    excerpt_max_chars: int = EXCERPT_MAX_CHARS_DEFAULT,
) -> APIRouter:
    async def _run_retrieve_documents(
        arguments: dict[str, Any], user_id: str | None
    ) -> dict[str, Any]:
        validate_against(_INPUT_VALIDATOR, arguments)
        try:
            await retrieve_v2_service.assert_owner(user_id, arguments["document_id_list"])
        except DocumentForbidden as exc:
            raise McpToolError(
                TOOL_FORBIDDEN,
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
                min_score=arguments.get("min_score", DEFAULT_MIN_SCORE),
            )
        except Exception as exc:
            logger.exception("mcp.tool.error", tool="retrieve", error_type=type(exc).__name__)
            raise McpToolError(
                TOOL_EXECUTION_FAILED,
                HttpErrorCode.MCP_TOOL_EXECUTION_FAILED.value,
                str(exc) or "tool execution failed",
            ) from exc
        # Post-filter: _FeedbackMemoryRetriever ignores the Haystack filters argument
        # and can return chunks from documents outside the requested set.
        allowed = set(arguments["document_id_list"])
        docs = [d for d in docs if d.meta and d.meta.get("document_id") in allowed]
        entries = [doc_to_source_entry(d, max_chars=excerpt_max_chars) for d in docs]
        return {
            "content": [{"type": "text", "text": render_context_markdown(entries)}],
            "structuredContent": {"sources": entries},
            "isError": False,
        }

    async def _run_create_skill(arguments: dict[str, Any], user_id: str | None) -> dict[str, Any]:
        if not user_id:
            raise McpToolError(
                INVALID_PARAMS,
                HttpErrorCode.MISSING_USER_ID.value,
                "user identity required to create a skill",
            )
        validate_against(_CREATE_SKILL_INPUT_VALIDATOR, arguments)
        try:
            resp = await skill_service.create(
                user_id=user_id,
                name=arguments["name"],
                description=arguments.get("description", ""),
                instructions=arguments["instructions"],
                enabled=arguments.get("enabled", True),
            )
        except SkillNameConflictError as exc:
            raise McpToolError(
                INVALID_PARAMS, HttpErrorCode.SKILL_NAME_CONFLICT.value, str(exc)
            ) from exc
        except Exception as exc:
            logger.exception("mcp.tool.error", tool="create_skill", error_type=type(exc).__name__)
            raise McpToolError(
                TOOL_EXECUTION_FAILED,
                HttpErrorCode.MCP_TOOL_EXECUTION_FAILED.value,
                str(exc) or "tool execution failed",
            ) from exc
        skill = {
            "skill_id": resp.skill_id,
            "name": resp.name,
            "description": resp.description,
            "enabled": resp.enabled,
            "readonly": resp.readonly,
        }
        text = (
            f"Created skill '{resp.name}' (skill_id={resp.skill_id}). "
            "The user can select it for a future chat turn."
        )
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"skill": skill},
            "isError": False,
        }

    tools: list[Tool] = [RETRIEVE_DOCUMENTS_TOOL]
    tool_handlers: dict[str, ToolHandler] = {"retrieve": _run_retrieve_documents}
    if skill_service is not None:
        tools.append(CREATE_SKILL_TOOL)
        tool_handlers["create_skill"] = _run_create_skill

    return create_jsonrpc_router("/mcp/v1", tools, tool_handlers)
