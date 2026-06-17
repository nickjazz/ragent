"""Unit tests for make_command_dep()."""

from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse

from ragent.commands import CommandRegistry
from ragent.commands._deps import _noop_dep, make_command_dep


def _make_request(body: bytes, headers: dict[str, str] | None = None) -> Request:
    req = MagicMock(spec=Request)
    req.body = AsyncMock(return_value=body)
    req.scope = {}
    h = headers or {}
    req.headers.get = MagicMock(side_effect=lambda key, default="": h.get(key.lower(), default))
    return req


def _registry_returning(gen: Generator | None) -> CommandRegistry:
    registry = MagicMock(spec=CommandRegistry)
    registry.dispatch.return_value = gen
    return registry


def _gen() -> Generator[str, None, None]:
    yield "data: hello\n\n"


_VALID_BODY = json.dumps(
    {
        "runId": "r1",
        "threadId": "t1",
        "messages": [{"id": "m1", "role": "user", "content": "/admin-quality-validation"}],
    }
).encode()


@pytest.mark.asyncio
async def test_matched_returns_streaming_response() -> None:
    dep = make_command_dep(_registry_returning(_gen()), "x-auth-token")
    request = _make_request(_VALID_BODY, {"x-user-id": "u1", "x-auth-token": "Bearer tok"})
    result = await dep(request)
    assert isinstance(result, StreamingResponse)


@pytest.mark.asyncio
async def test_unmatched_returns_none() -> None:
    dep = make_command_dep(_registry_returning(None), "x-auth-token")
    request = _make_request(_VALID_BODY)
    result = await dep(request)
    assert result is None


@pytest.mark.asyncio
async def test_bad_json_returns_none() -> None:
    dep = make_command_dep(_registry_returning(_gen()), "x-auth-token")
    request = _make_request(b"not json")
    result = await dep(request)
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_receives_correct_kwargs() -> None:
    registry = MagicMock(spec=CommandRegistry)
    registry.dispatch.return_value = None
    dep = make_command_dep(registry, "x-auth-token")
    request = _make_request(
        _VALID_BODY,
        {"x-user-id": "u42", "x-auth-token": "Bearer secret"},
    )
    await dep(request)
    registry.dispatch.assert_called_once()
    _, kwargs = registry.dispatch.call_args
    assert kwargs["user_id"] == "u42"
    assert kwargs["auth_header"] == "Bearer secret"
    assert kwargs["run_id"] == "r1"
    assert kwargs["thread_id"] == "t1"
    assert kwargs["jwt_header"] == "x-auth-token"


@pytest.mark.asyncio
async def test_mints_thread_id_when_client_omits_it() -> None:
    """Mirrors the route's own new_id() fallback (Model B) — the dependency
    resolves before that route code runs, so it must mint independently."""
    registry = MagicMock(spec=CommandRegistry)
    registry.dispatch.return_value = None
    dep = make_command_dep(registry, "x-auth-token")
    body = json.dumps(
        {
            "runId": "r1",
            "messages": [{"id": "m1", "role": "user", "content": "/admin-quality-validation"}],
        }
    ).encode()
    await dep(_make_request(body))
    _, kwargs = registry.dispatch.call_args
    assert kwargs["thread_id"]


@pytest.mark.asyncio
async def test_falls_back_to_anonymous_when_no_user_id() -> None:
    registry = MagicMock(spec=CommandRegistry)
    registry.dispatch.return_value = None
    dep = make_command_dep(registry, "x-auth-token")
    await dep(_make_request(_VALID_BODY))
    _, kwargs = registry.dispatch.call_args
    assert kwargs["user_id"] == "anonymous"


@pytest.mark.asyncio
async def test_noop_dep_returns_none() -> None:
    request = _make_request(b"")
    result = await _noop_dep(request)
    assert result is None
