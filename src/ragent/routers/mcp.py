"""MCP server router — `POST /mcp/v1` JSON-RPC 2.0 (§3.8, B47).

Methods: initialize / notifications/initialized / tools/list / tools/call / ping.
Tools: `retrieve` (wraps POST /retrieve/v1) and, when a skill service is
wired, `create_skill`. The JSON-RPC transport itself lives in
routers/mcp_transport.py (shared with /mcp/v2).
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
    DEFAULT_TOP_K,
    EXCERPT_MAX_CHARS_DEFAULT,
    build_attachment_exclusion_filter,
    build_es_filters,
    combine_filters,
    dedupe_by_document,
    doc_to_source_entry,
    run_retrieval,
)
from ragent.routers.mcp_tools.context_render import render_context_markdown
from ragent.routers.mcp_tools.create_skill import CREATE_SKILL_TOOL
from ragent.routers.mcp_tools.retrieve import RETRIEVE_TOOL
from ragent.routers.mcp_transport import (
    INVALID_PARAMS,
    TOOL_EXECUTION_FAILED,
    McpToolError,
    ToolHandler,
    create_jsonrpc_router,
    validate_against,
)
from ragent.services.skill_service import SkillNameConflictError

logger = structlog.get_logger(__name__)

# Uses the same inputSchema already advertised by tools/list — schema and
# validation can never drift apart.
_RETRIEVE_INPUT_VALIDATOR = Draft7Validator(RETRIEVE_TOOL.inputSchema)
_CREATE_SKILL_INPUT_VALIDATOR = Draft7Validator(CREATE_SKILL_TOOL.inputSchema)


def create_mcp_router(
    retrieval_pipeline: Any,
    *,
    skill_service: Any | None = None,
    excerpt_max_chars: int = EXCERPT_MAX_CHARS_DEFAULT,
) -> APIRouter:
    async def _run_retrieve(arguments: dict[str, Any], _user_id: str | None) -> dict[str, Any]:
        validate_against(_RETRIEVE_INPUT_VALIDATOR, arguments)
        try:
            docs = await run_in_threadpool(
                run_retrieval,
                retrieval_pipeline,
                query=arguments["query"],
                filters=combine_filters(
                    build_es_filters(arguments.get("source_app"), arguments.get("source_meta")),
                    build_attachment_exclusion_filter(),
                ),
                top_k=arguments.get("top_k", DEFAULT_TOP_K),
                min_score=arguments.get("min_score"),
            )
        except Exception as exc:
            logger.exception("mcp.tool.error", tool="retrieve", error_type=type(exc).__name__)
            raise McpToolError(
                TOOL_EXECUTION_FAILED,
                HttpErrorCode.MCP_TOOL_EXECUTION_FAILED.value,
                str(exc) or "tool execution failed",
            ) from exc
        if arguments.get("dedupe"):
            docs = dedupe_by_document(docs)
        entries = [doc_to_source_entry(d, max_chars=excerpt_max_chars) for d in docs]
        # Dual channel: sources JSON for the UI, markdown digest for the LLM.
        return {
            "content": [{"type": "text", "text": render_context_markdown(entries)}],
            "structuredContent": {"sources": entries},
            "isError": False,
        }

    async def _run_create_skill(arguments: dict[str, Any], user_id: str | None) -> dict[str, Any]:
        # Owner is the authenticated caller — NEVER a tool argument. Fail closed
        # when no identity reached the MCP endpoint so a skill can never be
        # created under an unknown or attacker-chosen owner.
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
            # Mirror _run_retrieve: an unexpected failure (DB down, write error)
            # surfaces as a JSON-RPC error envelope, not an HTTP 500.
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

    tools: list[Tool] = [RETRIEVE_TOOL]
    tool_handlers: dict[str, ToolHandler] = {"retrieve": _run_retrieve}
    if skill_service is not None:
        tools.append(CREATE_SKILL_TOOL)
        tool_handlers["create_skill"] = _run_create_skill

    return create_jsonrpc_router("/mcp/v1", tools, tool_handlers)
