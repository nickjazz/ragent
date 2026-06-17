"""FastAPI dependency factory for slash command dispatch."""

from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import StreamingResponse
from twp_ai.schemas import Message

from ragent.auth.deps import get_user_id
from ragent.commands import CommandRegistry
from ragent.utility.id_gen import new_id


def make_command_dep(registry: CommandRegistry, jwt_header: str) -> Callable:
    """Return a FastAPI dependency that resolves to StreamingResponse | None."""

    async def _dep(request: Request) -> StreamingResponse | None:
        # request.body() is idempotent — Starlette caches in request._body after
        # first read; FastAPI has already consumed it for body: RunAgentInput parsing.
        body_bytes = await request.body()
        try:
            payload = json.loads(body_bytes)
        except Exception:
            return None
        messages = [Message(**m) for m in payload.get("messages", [])]
        x_user_id = await get_user_id(request)
        auth_header = request.headers.get(jwt_header.lower(), "")
        # Mirrors the route's own thread_id-minting fallback (Model B — ragent
        # owns the session id): this dependency resolves before that code runs,
        # so a thread_id-bearing command never sees the route's minted value.
        result = registry.dispatch(
            messages,
            user_id=x_user_id or "anonymous",
            auth_header=auth_header,
            run_id=payload.get("runId", ""),
            thread_id=payload.get("threadId") or new_id(),
            jwt_header=jwt_header,
        )
        return StreamingResponse(result, media_type="text/event-stream") if result else None

    return _dep


async def _noop_dep(request: Request) -> None:  # noqa: ARG001
    return None
