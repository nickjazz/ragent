"""Shared JSON-RPC 2.0 transport for the MCP router (/mcp/v1).

Extracted verbatim from routers/mcp.py so this hardened envelope
implementation is reusable if a future MCP surface is added: body-size cap,
parse/invalid-request mapping, notification semantics, initialize/ping,
tools/list, and the tools/call dispatch that threads the authenticated
caller id into handlers.

Routers own their tool sets and handlers; this module owns the wire protocol.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from jsonschema import Draft7Validator, ValidationError
from mcp.types import Tool

from ragent import __version__ as _RAGENT_VERSION
from ragent.auth.deps import get_user_id
from ragent.errors.codes import HttpErrorCode
from ragent.errors.problem import problem
from ragent.utility.env import int_env

logger = structlog.get_logger(__name__)

# JSON-RPC 2.0 standard error codes (spec §3.8.4).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
TOOL_EXECUTION_FAILED = -32001
TOOL_FORBIDDEN = -32002  # ownership / authorization failure (not a schema error)


class McpToolError(Exception):
    """Raised by handlers to surface a JSON-RPC error envelope."""

    def __init__(self, code: int, error_code: str, message: str) -> None:
        self.code = code
        self.error_code = error_code
        super().__init__(message)


# MCP protocol revisions (B47/B63). Code constants, not env-driven — operators
# flipping these would silently break the contract advertised in `initialize`.
# Newest first; 2025-06-18 is the first revision with tool outputSchema /
# structuredContent (older clients ignore those additive result fields).
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
_MCP_SERVER_NAME = "ragent"

# Defence-in-depth body-size cap. Production ingress (nginx / ALB) is the
# canonical bound; this is the second line. 256 KiB easily holds a fat
# JSON-RPC envelope (multi-kB query + nested filters) and rejects abuse.
# Env-tunable per the §4.6 inventory rule; mirrors `INGEST_INLINE_MAX_BYTES`.
_MAX_REQUEST_BYTES = int_env("MCP_REQUEST_MAX_BYTES", 256 * 1024)


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


async def _handle_initialize(params: Any) -> dict[str, Any]:
    # MCP version negotiation: echo the requested revision when supported,
    # otherwise answer with the latest we speak (mirrors the official SDK).
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    version = (
        requested if requested in SUPPORTED_PROTOCOL_VERSIONS else (SUPPORTED_PROTOCOL_VERSIONS[0])
    )
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": _MCP_SERVER_NAME, "version": _RAGENT_VERSION},
    }


def validate_against(validator: Draft7Validator, args: Any) -> None:
    """Validate `tools/call` arguments against a tool's inputSchema.

    Raises `McpToolError(-32602, MCP_TOOL_INPUT_INVALID, ...)` on the first
    failure. `additionalProperties:false` on each schema means stray fields
    (e.g. a spoofed ``user_id``) are rejected here, not silently ignored. Draft7
    is used because the schemas use `default`/`minimum`/`maximum`/`required`.
    """
    try:
        validator.validate(args)
    except ValidationError as exc:
        # `path` is the dotted location inside `args`; the root error has no
        # path so we substitute "arguments" for a readable message.
        location = ".".join(str(p) for p in exc.absolute_path) or "arguments"
        raise McpToolError(
            INVALID_PARAMS,
            HttpErrorCode.MCP_TOOL_INPUT_INVALID.value,
            f"{location}: {exc.message}",
        ) from exc


# Stateless handlers — no per-router state needed. `tools/list` and `tools/call`
# are built per router because the tool set is a constructor concern, and
# tools/call needs the per-request caller id.
_StatelessHandler = Callable[[Any], Awaitable[dict[str, Any]]]
_STATELESS_METHODS: dict[str, _StatelessHandler] = {
    "ping": _handle_ping,
    "initialize": _handle_initialize,
}

# A tool handler receives (validated-shape params arguments, caller user_id).
ToolHandler = Callable[[dict[str, Any], "str | None"], Awaitable[dict[str, Any]]]


def create_jsonrpc_router(
    prefix: str,
    tools: list[Tool],
    tool_handlers: dict[str, ToolHandler],
) -> APIRouter:
    """Build an APIRouter serving the full MCP JSON-RPC surface at `prefix`."""
    router = APIRouter(prefix=prefix)

    # tools/list is read-only; the set is fixed per router instance, so the
    # payload is built once here.
    tools_list_payload = [t.model_dump(exclude_none=True) for t in tools]

    async def _handle_tools_list(_params: Any) -> dict[str, Any]:
        return {"tools": tools_list_payload}

    async def _handle_tools_call(params: Any, user_id: str | None) -> dict[str, Any]:
        # JSON-RPC 2.0 allows `params` to be omitted, an object, or an array.
        # We only accept the object form (named arguments); array/positional
        # surfaces as -32602 rather than a 500 AttributeError on `.get()`.
        if params is None:
            params = {}
        elif not isinstance(params, dict):
            raise McpToolError(
                INVALID_PARAMS,
                HttpErrorCode.MCP_TOOL_INPUT_INVALID.value,
                "`params` must be an object (named arguments)",
            )
        handler = tool_handlers.get(params.get("name"))
        if handler is None:
            raise McpToolError(
                INVALID_PARAMS,
                HttpErrorCode.MCP_TOOL_NOT_FOUND.value,
                f"unknown tool: {params.get('name')!r}",
            )
        # Distinguish "omitted/null" (→ {}) from a falsy non-object like [] / "" /
        # False: `or {}` would silently coerce the latter to {} and bypass the
        # type check, accepting an invalid arguments shape.
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        elif not isinstance(arguments, dict):
            raise McpToolError(
                INVALID_PARAMS,
                HttpErrorCode.MCP_TOOL_INPUT_INVALID.value,
                "`arguments` must be an object",
            )
        return await handler(arguments, user_id)

    methods: dict[str, _StatelessHandler] = {
        **_STATELESS_METHODS,
        "tools/list": _handle_tools_list,
    }

    @router.post("")
    async def mcp_jsonrpc(request: Request) -> Response:
        # 413 Payload Too Large — transport-layer concern (NOT a JSON-RPC
        # error envelope) so the caller can distinguish from in-protocol
        # errors. Mirrors the §3.8.1 auth-failure rule.
        #
        # Pre-read on Content-Length where the client advertised one — avoids
        # buffering an attacker-sized body into memory before rejecting it.
        # Fall back to a post-read length check (covers clients that omit
        # Content-Length, e.g. chunked transfer).
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
            # UnicodeDecodeError fires when the body isn't valid UTF-8; per
            # JSON-RPC 2.0 §5, an unparseable body is a Parse error (-32700)
            # with id:null, NOT a transport 500.
            return JSONResponse(
                _jsonrpc_error(
                    None,
                    PARSE_ERROR,
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
                    INVALID_REQUEST,
                    "Invalid Request",
                    data={"error_code": HttpErrorCode.MCP_INVALID_REQUEST.value},
                )
            )

        method = envelope["method"]
        # JSON-RPC 2.0 §4.1: a notification is a request WITHOUT the `id`
        # member. `id: null` is a valid request, not a notification.
        is_notification = "id" not in envelope
        req_id = envelope.get("id")

        if is_notification:
            # No JSON-RPC response object for notifications — even when the
            # method name is unrecognised. HTTP 204 is the streamable-HTTP
            # transport mapping.
            return Response(status_code=204)

        # tools/call is dispatched separately because it needs the per-request
        # caller identity; the other methods do not.
        is_tools_call = method == "tools/call"
        handler = methods.get(method)
        if handler is None and not is_tools_call:
            return JSONResponse(
                _jsonrpc_error(
                    req_id,
                    METHOD_NOT_FOUND,
                    f"Method not found: {method}",
                    data={"error_code": HttpErrorCode.MCP_METHOD_NOT_FOUND.value},
                )
            )

        try:
            if is_tools_call:
                user_id = await get_user_id(request)
                result = await _handle_tools_call(envelope.get("params"), user_id)
            else:
                result = await handler(envelope.get("params"))
        except McpToolError as exc:
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
