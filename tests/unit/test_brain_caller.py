"""T-BRAIN.2 — BrainCaller (twp-ai-native relay to the ragent-brain upstream)."""

from __future__ import annotations

import json

import httpx
import pytest
from twp_ai.schemas import RunAgentInput

from ragent.clients.brain_caller import BrainCaller, build_brain_headers
from ragent.errors.codes import HttpErrorCode
from ragent.errors.upstream import UpstreamServiceError, UpstreamTimeoutError


def test_build_brain_headers_drops_case_insensitive_service_collisions() -> None:
    out = build_brain_headers(
        "alice", "sekret", {"x-user-id": "mallory", "X-Brain-Key": "forged", "X-Auth-Token": "t"}
    )
    assert out == {"X-User-Id": "alice", "X-Brain-Key": "sekret", "X-Auth-Token": "t"}


def test_build_brain_headers_handles_none_and_missing_brain_key() -> None:
    assert build_brain_headers("alice", None, None) == {"X-User-Id": "alice"}


def _request() -> RunAgentInput:
    return RunAgentInput.model_validate(
        {
            "threadId": "thread_1",
            "runId": "run_1",
            "messages": [{"id": "m1", "role": "user", "content": "hi"}],
            "tools": [],
            "state": None,
            "context": [],
            "forwardedProps": None,
        }
    )


def _caller(handler, *, brain_key: str | None = "sekret", user_id: str = "alice") -> BrainCaller:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return BrainCaller(
        http_client=client,
        brain_url="http://brain:8100",
        user_id=user_id,
        brain_key=brain_key,
        timeout=5.0,
    )


def test_relays_brain_sse_frames_verbatim() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = request.read().decode()
        body = (
            'data: {"type":"RUN_STARTED","runId":"run_1","threadId":"thread_1"}\n\n'
            'data: {"type":"RUN_FINISHED","runId":"run_1","threadId":"thread_1"}\n\n'
        )
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    frames = list(_caller(handler).stream_frames(_request(), ""))

    assert frames == [
        'data: {"type":"RUN_STARTED","runId":"run_1","threadId":"thread_1"}\n\n',
        'data: {"type":"RUN_FINISHED","runId":"run_1","threadId":"thread_1"}\n\n',
    ]
    # posts to /run with the service + user headers and the camelCase body verbatim.
    assert seen["url"] == "http://brain:8100/run"
    assert seen["headers"]["x-user-id"] == "alice"
    assert seen["headers"]["x-brain-key"] == "sekret"
    assert '"runId":"run_1"' in seen["body"].replace(" ", "")


def test_no_brain_key_header_when_unset() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(200, content=b"", headers={"content-type": "text/event-stream"})

    list(_caller(handler, brain_key=None).stream_frames(_request(), ""))
    assert "x-brain-key" not in seen["headers"]


def test_forwards_extra_headers_to_run() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(200, content=b"", headers={"content-type": "text/event-stream"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    caller = BrainCaller(
        http_client=client,
        brain_url="http://brain:8100",
        user_id="alice",
        brain_key="sekret",
        extra_headers={"X-Auth-Token": "jwt-abc"},
        timeout=5.0,
    )
    list(caller.stream_frames(_request(), ""))
    # forwarded auth header rides alongside the service + user headers.
    assert seen["headers"]["x-auth-token"] == "jwt-abc"
    assert seen["headers"]["x-user-id"] == "alice"
    assert seen["headers"]["x-brain-key"] == "sekret"


def test_extra_headers_cannot_override_service_headers() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        return httpx.Response(200, content=b"", headers={"content-type": "text/event-stream"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    caller = BrainCaller(
        http_client=client,
        brain_url="http://brain:8100",
        user_id="alice",
        brain_key="sekret",
        # forged service headers in DIFFERENT casing must NOT ride along: a
        # case-sensitive dict merge would otherwise leave httpx emitting BOTH
        # `x-user-id: mallory` and `X-User-Id: alice`, and a FastAPI brain reads
        # the first — defeating the override. The collision must be dropped.
        extra_headers={"x-user-id": "mallory", "x-brain-key": "forged"},
        timeout=5.0,
    )
    list(caller.stream_frames(_request(), ""))
    # exactly one value per service header — no duplicate case variant emitted.
    assert seen["headers"].get_list("x-user-id") == ["alice"]
    assert seen["headers"].get_list("x-brain-key") == ["sekret"]


def test_timeout_raises_typed_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    with pytest.raises(UpstreamTimeoutError) as exc:
        list(_caller(handler).stream_frames(_request(), ""))
    assert exc.value.error_code == HttpErrorCode.BRAINAGENT_TIMEOUT


def test_non_2xx_raises_typed_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    with pytest.raises(UpstreamServiceError) as exc:
        list(_caller(handler).stream_frames(_request(), ""))
    assert exc.value.error_code == HttpErrorCode.BRAINAGENT_UPSTREAM_ERROR


def test_preserves_required_null_fields() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.read())
        return httpx.Response(200, content=b"", headers={"content-type": "text/event-stream"})

    list(_caller(handler).stream_frames(_request(), ""))
    # state/forwardedProps are required (nullable) in RunAgentInput — the relayed
    # body must keep the keys with null values, not drop them (else brain 422s).
    assert seen["body"]["state"] is None
    assert seen["body"]["forwardedProps"] is None


def test_injects_model_only_when_body_has_none() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(200, content=b"", headers={"content-type": "text/event-stream"})

    list(_caller(handler).stream_frames(_request(), "gpt-x"))
    assert '"model":"gpt-x"' in seen["body"].replace(" ", "")
