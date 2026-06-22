"""T-CAv3.4 — chatagent v3 router (twp-ai protocol proxy) unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragent.clients.chat_stream_store import ChatStreamStore
from ragent.clients.rate_limiter import RateLimiter, RateLimitResult
from ragent.errors.codes import HttpErrorCode
from ragent.routers.chatagent_v3 import create_chatagent_v3_router
from tests.helpers import done_line as _done_line
from tests.helpers import msg_line as _msg_line
from tests.helpers import parse_sse_events as _events
from tests.helpers import parse_sse_ids as _ids
from tests.helpers import resp_mock as _resp_mock


def _make_app(
    *, rate_limiter: RateLimiter | None = None, chat_stream_store: ChatStreamStore | None = None
):
    http_mock = MagicMock(spec=httpx.Client)
    app = FastAPI()
    router = create_chatagent_v3_router(
        http_client=http_mock,
        chatagent_ap_name="TestAP",
        chatagent_auth="Bearer up",
        chatagent_api_url="http://upstream",
        rate_limiter=rate_limiter,
        chat_stream_store=chat_stream_store,
        stream_idle_timeout=3.0,
    )
    app.include_router(router)
    return app, http_mock


def _store() -> ChatStreamStore:
    return ChatStreamStore(fakeredis.FakeStrictRedis(decode_responses=True))


def _run_input() -> dict:
    return {
        "threadId": "thread_1",
        "runId": "run_1",
        "messages": [{"id": "m1", "role": "user", "content": "What are the features?"}],
        "tools": [],
        "state": None,
        "context": [],
        "forwardedProps": None,
    }


def test_v3_mints_thread_id_when_omitted() -> None:
    # Model B: client omits threadId on a new conversation; ragent mints it,
    # echoes it in RUN_STARTED, and sends it as the upstream session.
    app, http_mock = _make_app()
    http_mock.send.return_value = _resp_mock([_msg_line("hi", message_id="m1"), _done_line()])
    body = _run_input()
    del body["threadId"]

    with TestClient(app) as client:
        r = client.post("/chatagent/v3", json=body, headers={"X-User-Id": "alice"})

    events = _events(r.text)
    minted = events[0]["threadId"]
    assert minted  # non-empty minted id surfaced to the client
    sent = http_mock.build_request.call_args.kwargs["json"]
    assert sent["metadata"]["session"] == minted  # upstream gets ours, not its own


def test_v3_uses_supplied_thread_id() -> None:
    app, http_mock = _make_app()
    http_mock.send.return_value = _resp_mock([_msg_line("hi", message_id="m1"), _done_line()])

    with TestClient(app) as client:
        r = client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "alice"})

    events = _events(r.text)
    assert events[0]["threadId"] == "thread_1"
    sent = http_mock.build_request.call_args.kwargs["json"]
    assert sent["metadata"]["session"] == "thread_1"


def test_v3_streams_twp_ai_event_lifecycle() -> None:
    app, http_mock = _make_app()
    http_mock.send.return_value = _resp_mock(
        [
            _msg_line("The ", message_id="msg-1"),
            _msg_line("features", message_id="msg-1"),
            _done_line(),
        ]
    )

    with TestClient(app) as client:
        r = client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "alice"})

    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    events = _events(r.text)
    assert [e["type"] for e in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert [e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"] == [
        "The ",
        "features",
    ]
    assert events[0]["runId"] == "run_1"
    assert events[0]["threadId"] == "thread_1"


def test_v3_planner_message_streams_reasoning_events() -> None:
    app, http_mock = _make_app()
    http_mock.send.return_value = _resp_mock(
        [
            _msg_line("Planning ", message_id="plan-1", agent_type="planner"),
            _msg_line("steps", message_id="plan-1", agent_type="planner"),
            _msg_line("The answer.", message_id="sum-1", agent_type="summarizer"),
            _done_line(),
        ]
    )

    with TestClient(app) as client:
        r = client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "alice"})

    assert r.status_code == 200
    events = _events(r.text)
    assert [e["type"] for e in events] == [
        "RUN_STARTED",
        "REASONING_START",
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_END",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert [e["delta"] for e in events if e["type"] == "REASONING_MESSAGE_CONTENT"] == [
        "Planning ",
        "steps",
    ]


def test_v3_injects_server_metadata() -> None:
    app, http_mock = _make_app()
    http_mock.send.return_value = _resp_mock([_done_line()])

    with TestClient(app) as client:
        client.post(
            "/chatagent/v3",
            json=_run_input(),
            headers={"X-User-Id": "bob", "X-Auth-Token": "tok-bob"},
        )

    payload = http_mock.build_request.call_args.kwargs["json"]
    assert payload["metadata"]["apName"] == "TestAP"
    assert payload["metadata"]["user"] == "bob"
    assert payload["metadata"]["userToken"] == "tok-bob"
    assert payload["metadata"]["session"] == "thread_1"
    assert payload["inputData"]["message"] == "What are the features?"
    assert payload["stream"] is True


def test_v3_rate_limited_emits_run_error_not_http_429() -> None:
    rl_mock = MagicMock(spec=RateLimiter)
    result = MagicMock(spec=RateLimitResult)
    result.allowed = False
    result.reset_at = 9999999999.0
    rl_mock.check.return_value = result

    app, http_mock = _make_app(rate_limiter=rl_mock)
    with TestClient(app) as client:
        r = client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "dave"})

    assert r.status_code == 200
    events = _events(r.text)
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == HttpErrorCode.CHATAGENT_RATE_LIMITED
    assert events[-1]["runId"] == "run_1"
    http_mock.send.assert_not_called()


def test_v3_upstream_error_emits_run_error() -> None:
    app, http_mock = _make_app()
    http_mock.send.side_effect = httpx.RequestError("conn refused")

    with TestClient(app) as client:
        r = client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "alice"})

    assert r.status_code == 200
    events = _events(r.text)
    assert events[0]["type"] == "RUN_STARTED"
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == HttpErrorCode.CHATAGENT_UPSTREAM_ERROR


def test_v3_resumable_post_streams_same_lifecycle_with_event_ids() -> None:
    # With a store wired the POST streams through the Redis buffer: the twp-ai
    # event sequence is unchanged, but every frame now carries an SSE `id:` so
    # the client can resume from it.
    store = _store()
    app, http_mock = _make_app(chat_stream_store=store)
    http_mock.send.return_value = _resp_mock(
        [
            _msg_line("The ", message_id="msg-1"),
            _msg_line("answer", message_id="msg-1"),
            _done_line(),
        ]
    )

    with TestClient(app) as client:
        r = client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "alice"})

    events = _events(r.text)
    assert [e["type"] for e in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    # One id per emitted frame, all distinct — the resume cursor.
    ids = _ids(r.text)
    assert len(ids) == len(events)
    assert len(set(ids)) == len(ids)


def test_v3_reconnect_resumes_after_last_event_id() -> None:
    # Simulate a drop after the first content frame, then reconnect with that
    # frame's id: only the strictly-later frames replay.
    store = _store()
    app, http_mock = _make_app(chat_stream_store=store)
    http_mock.send.return_value = _resp_mock(
        [
            _msg_line("The ", message_id="msg-1"),
            _msg_line("answer", message_id="msg-1"),
            _done_line(),
        ]
    )

    with TestClient(app) as client:
        first = client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "alice"})
        # The producer has finished and the buffer is retained within its TTL.
        ids = _ids(first.text)
        resume_from = ids[1]  # after RUN_STARTED + first frame boundary

        r = client.get(
            "/chatagent/v3/reconnect",
            params={"thread_id": "thread_1", "run_id": "run_1"},
            headers={"X-User-Id": "alice", "Last-Event-ID": resume_from},
        )

    replayed = _events(r.text)
    # Everything up to and including `resume_from` is excluded.
    assert "RUN_STARTED" not in [e["type"] for e in replayed]
    assert replayed[-1]["type"] == "RUN_FINISHED"
    resumed_ids = _ids(r.text)
    assert all(i > resume_from for i in resumed_ids)


def test_v3_reconnect_unknown_run_emits_stream_expired() -> None:
    store = _store()
    app, _ = _make_app(chat_stream_store=store)

    with TestClient(app) as client:
        r = client.get(
            "/chatagent/v3/reconnect",
            params={"thread_id": "thread_x", "run_id": "run_x"},
            headers={"X-User-Id": "alice"},
        )

    events = _events(r.text)
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == HttpErrorCode.CHATAGENT_STREAM_EXPIRED


def test_v3_duplicate_run_id_spawns_single_producer() -> None:
    # The SET NX lock means a retried POST with the same runId reuses the buffer
    # instead of generating again — the upstream is called exactly once.
    store = _store()
    app, http_mock = _make_app(chat_stream_store=store)
    http_mock.send.return_value = _resp_mock([_msg_line("hi", message_id="m1"), _done_line()])

    with TestClient(app) as client:
        first = client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "alice"})
        second = client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "alice"})

    assert http_mock.send.call_count == 1
    # Both responses replay the same buffered run.
    assert [e["type"] for e in _events(first.text)] == [e["type"] for e in _events(second.text)]


def test_v3_reconnect_is_owner_scoped() -> None:
    # A different user cannot reconnect to alice's run even with the right ids.
    store = _store()
    app, http_mock = _make_app(chat_stream_store=store)
    http_mock.send.return_value = _resp_mock([_msg_line("hi", message_id="m1"), _done_line()])

    with TestClient(app) as client:
        client.post("/chatagent/v3", json=_run_input(), headers={"X-User-Id": "alice"})
        r = client.get(
            "/chatagent/v3/reconnect",
            params={"thread_id": "thread_1", "run_id": "run_1"},
            headers={"X-User-Id": "mallory"},
        )

    events = _events(r.text)
    assert events[-1]["code"] == HttpErrorCode.CHATAGENT_STREAM_EXPIRED
