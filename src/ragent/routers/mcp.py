"""MCP server router for `POST /mcp/v1` JSON-RPC 2.0."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ragent import __version__ as _RAGENT_VERSION
from ragent.errors.codes import HttpErrorCode
from ragent.errors.problem import problem
from ragent.mcp_tools.registry import McpToolInputInvalid, McpToolNotFound, McpToolRegistry
from ragent.mcp_tools.retrieve import build_retrieve_tool
from ragent.pipelines.retrieve import EXCERPT_MAX_CHARS_DEFAULT, run_retrieval
from ragent.utility.env import int_env

logger = structlog.get_logger(__name__)

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_TOOL_EXECUTION_FAILED = -32001

_MCP_PROTOCOL_VERSION = "2024-11-05"
_MCP_SERVER_NAME = "ragent"
_MAX_REQUEST_BYTES = int_env("MCP_REQUEST_MAX_BYTES", 256 * 1024)


class _McpToolError(Exception):
    """Raised by handlers to surface a JSON-RPC error envelope."""

    def __init__(self, code: int, error_code: str, message: str) -> None:
        self.code = code
        self.error_code = error_code
        super().__init__(message)


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(
    req_id: Any,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


async def _handle_ping(_params: Any) -> dict[str, Any]:
    return {}


async def _handle_initialize(_params: Any) -> dict[str, Any]:
    return {
        "protocolVersion": _MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": _MCP_SERVER_NAME, "version": _RAGENT_VERSION},
    }


def create_mcp_router(
    retrieval_pipeline: Any,
    *,
    excerpt_max_chars: int = EXCERPT_MAX_CHARS_DEFAULT,
) -> APIRouter:
    router = APIRouter(prefix="/mcp/v1")
    registry = McpToolRegistry(
        [
            build_retrieve_tool(
                retrieval_pipeline,
                excerpt_max_chars=excerpt_max_chars,
                run_retrieval_fn=run_retrieval,
            )
        ]
    )

    async def _handle_tools_list(_params: Any) -> dict[str, Any]:
        return {"tools": registry.list_tools()}

    async def _handle_tools_call(params: Any) -> dict[str, Any]:
        if params is None:
            params = {}
        elif not isinstance(params, dict):
            raise _McpToolError(
                _INVALID_PARAMS,
                HttpErrorCode.MCP_TOOL_INPUT_INVALID.value,
                "`params` must be an object (named arguments)",
            )
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            return await registry.call(name, arguments)
        except McpToolNotFound as exc:
            raise _McpToolError(
                _INVALID_PARAMS,
                HttpErrorCode.MCP_TOOL_NOT_FOUND.value,
                str(exc),
            ) from exc
        except McpToolInputInvalid as exc:
            raise _McpToolError(
                _INVALID_PARAMS,
                HttpErrorCode.MCP_TOOL_INPUT_INVALID.value,
                str(exc),
            ) from exc
        except Exception as exc:
            logger.exception("mcp.tool.error", tool=name, error_type=type(exc).__name__)
            raise _McpToolError(
                _TOOL_EXECUTION_FAILED,
                HttpErrorCode.MCP_TOOL_EXECUTION_FAILED.value,
                str(exc) or "tool execution failed",
            ) from exc

    methods: dict[str, Callable[[Any], Awaitable[dict[str, Any]]]] = {
        "ping": _handle_ping,
        "initialize": _handle_initialize,
        "tools/list": _handle_tools_list,
        "tools/call": _handle_tools_call,
    }

    @router.post("")
    async def mcp_jsonrpc(request: Request) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > _MAX_REQUEST_BYTES:
            return problem(
                413,
                HttpErrorCode.MCP_INVALID_REQUEST,
                f"request body exceeds {_MAX_REQUEST_BYTES} bytes",
            )
        raw = await request.body()
        if len(raw) > _MAX_REQUEST_BYTES:
            return problem(
                413,
                HttpErrorCode.MCP_INVALID_REQUEST,
                f"request body exceeds {_MAX_REQUEST_BYTES} bytes",
            )
        try:
            envelope = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(
                _jsonrpc_error(
                    None,
                    _PARSE_ERROR,
                    "Parse error",
                    data={"error_code": HttpErrorCode.MCP_PARSE_ERROR.value},
                )
            )

        if (
            not isinstance(envelope, dict)
            or envelope.get("jsonrpc") != "2.0"
            or not isinstance(envelope.get("method"), str)
        ):
            req_id = envelope.get("id") if isinstance(envelope, dict) else None
            return JSONResponse(
                _jsonrpc_error(
                    req_id,
                    _INVALID_REQUEST,
                    "Invalid Request",
                    data={"error_code": HttpErrorCode.MCP_INVALID_REQUEST.value},
                )
            )

        method = envelope["method"]
        is_notification = "id" not in envelope
        req_id = envelope.get("id")

        if is_notification:
            return Response(status_code=204)

        handler = methods.get(method)
        if handler is None:
            return JSONResponse(
                _jsonrpc_error(
                    req_id,
                    _METHOD_NOT_FOUND,
                    f"Method not found: {method}",
                    data={"error_code": HttpErrorCode.MCP_METHOD_NOT_FOUND.value},
                )
            )

        try:
            result = await handler(envelope.get("params"))
        except _McpToolError as exc:
            return JSONResponse(
                _jsonrpc_error(
                    req_id,
                    exc.code,
                    str(exc),
                    data={"error_code": exc.error_code},
                )
            )
        return JSONResponse(_jsonrpc_result(req_id, result))

    return router
