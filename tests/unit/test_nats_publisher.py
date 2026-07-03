"""T-CAv3N — NatsSessionPublisher: per-user live session-list status over NATS.

Auth is the backend **app flow** (mirrors mco-clean): an ephemeral Ed25519 nkey
is minted, POSTed to the NATS auth service with the app's `client_secret` +
`namespace`, exchanged for a NATS user JWT, then presented on connect with a
nonce-signing callback. Everything degrades to a no-op (snapshot-only) when
unconfigured or on any auth/connect failure.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import Future

import structlog.testing

from ragent.clients.nats_publisher import NatsSessionPublisher

_APP_KWARGS = dict(
    auth_service_url="https://auth.example",
    client_secret="sek",
    namespace="ragent",
)


def _publisher(**overrides) -> NatsSessionPublisher:
    return NatsSessionPublisher(**{"servers": "nats://x", **_APP_KWARGS, **overrides})


class _FakeNC:
    """Stand-in for a nats-py connection with the surface the supervisor touches."""

    def __init__(self) -> None:
        self.is_closed = False
        self.force_reconnect_calls = 0
        self.drained = False
        self.published: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, data: bytes) -> None:
        self.published.append((subject, data))

    async def drain(self) -> None:
        self.drained = True

    async def force_reconnect(self) -> None:
        self.force_reconnect_calls += 1


def test_subject_is_per_user_and_template_configurable() -> None:
    pub = _publisher(subject_template="session.{user}.status")
    assert pub.subject("alice") == "session.alice.status"
    assert pub.subject("bob") != pub.subject("alice")

    custom = _publisher(subject_template="twp.{user}.sess")
    assert custom.subject("alice") == "twp.alice.sess"


def test_auth_payload_is_app_flow_with_camelcase_public_key() -> None:
    pub = _publisher()
    payload = pub._auth_payload("UABC")  # noqa: SLF001
    assert payload == {
        "token_type": "app",
        "token": "sek",
        "namespace": "ragent",
        "publicKey": "UABC",
    }


def test_publish_is_noop_when_unconnected() -> None:
    # Before connect (or when unconfigured) → publish must not raise (snapshot-only degrade).
    pub = NatsSessionPublisher(
        servers=None, auth_service_url=None, client_secret=None, namespace=None
    )
    pub.publish("alice", {"session": "t1", "running": True})  # does not raise


def test_publish_schedules_event_to_the_connection() -> None:
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    received: list[tuple[str, bytes]] = []
    done = threading.Event()

    class _NC:
        async def publish(self, subject: str, data: bytes) -> None:
            received.append((subject, data))
            done.set()

    pub = _publisher()
    pub._nc = _NC()  # noqa: SLF001 — simulate a live connection
    pub._loop = loop  # noqa: SLF001

    pub.publish("alice", {"session": "t1", "running": True})

    assert done.wait(2.0)  # the producer-thread publish reached the loop's connection
    loop.call_soon_threadsafe(loop.stop)
    assert received == [
        ("session.alice.status", json.dumps({"session": "t1", "running": True}).encode())
    ]


def test_publish_fail_soft_on_broken_connection() -> None:
    # A connection without a usable publish (or a closed loop) must be swallowed.
    pub = _publisher()
    pub._nc = object()  # noqa: SLF001 — no .publish → AttributeError inside publish()
    pub._loop = asyncio.new_event_loop()  # noqa: SLF001
    pub.publish("alice", {"x": 1})  # does not raise


def test_publish_logs_when_the_scheduled_coroutine_raises() -> None:
    # run_coroutine_threadsafe returns immediately; a failure inside the
    # scheduled coroutine itself (e.g. a dropped NATS connection) lands on the
    # Future, not the synchronous try/except around scheduling — would
    # otherwise vanish with no log at all.
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    class _FailingNC:
        async def publish(self, subject: str, data: bytes) -> None:
            raise RuntimeError("connection dropped")

    pub = _publisher()
    pub._nc = _FailingNC()  # noqa: SLF001
    pub._loop = loop  # noqa: SLF001

    with structlog.testing.capture_logs() as logs:
        pub.publish("alice", {"session": "t1", "running": True})
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(
            e.get("event") == "nats.session_publish_failed" for e in logs
        ):
            time.sleep(0.01)

    loop.call_soon_threadsafe(loop.stop)
    assert any(e.get("event") == "nats.session_publish_failed" for e in logs)


def test_log_publish_failure_ignores_a_cancelled_future() -> None:
    # An asyncio/uvicorn lifespan shutdown can cancel outstanding scheduled
    # tasks; future.exception() raises CancelledError on a cancelled future,
    # which the callback must swallow rather than let escape (an exception
    # from a done-callback isn't fatal, but it's noisy — logged separately by
    # the stdlib logging module instead of the clean structlog warning).
    future: Future[None] = Future()
    future.cancel()

    with structlog.testing.capture_logs() as logs:
        NatsSessionPublisher._log_publish_failure(future)  # noqa: SLF001

    assert logs == []


async def test_connect_is_noop_when_unconfigured() -> None:
    pub = NatsSessionPublisher(
        servers=None, auth_service_url=None, client_secret=None, namespace=None
    )
    await pub.connect(asyncio.get_running_loop())
    pub.publish("alice", {"x": 1})  # still a no-op, no connection opened


async def test_connect_exchanges_jwt_then_opens_connection(monkeypatch) -> None:
    opened: dict[str, object] = {}

    class _NC:
        async def publish(self, subject: str, data: bytes) -> None: ...
        async def drain(self) -> None: ...

    async def _fake_connect(servers, **opts):  # noqa: ANN001
        opened["servers"] = servers
        opened["opts"] = opts
        return _NC()

    async def _fake_fetch(self, public_key):  # noqa: ANN001
        opened["public_key"] = public_key
        return "the.nats.jwt"

    monkeypatch.setattr("nats.connect", _fake_connect)
    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _fake_fetch)
    pub = _publisher(servers="nats://a, nats://b , ")

    await pub.connect(asyncio.get_running_loop())

    assert opened["servers"] == ["nats://a", "nats://b"]  # comma-split, stripped, empties dropped
    # JWT is presented verbatim; the signature callback returns base64 bytes.
    assert opened["opts"]["user_jwt_cb"]() == b"the.nats.jwt"
    sig = opened["opts"]["signature_cb"]("nonce")
    assert isinstance(sig, bytes) and sig  # signs the nonce → non-empty base64
    # The ephemeral public key (U…) was sent to the auth service.
    assert isinstance(opened["public_key"], str) and opened["public_key"].startswith("U")
    await pub.close()  # stop the supervisor the successful connect spawned


async def test_connect_fail_soft_does_not_abort(monkeypatch) -> None:
    async def _boom(self, public_key):  # noqa: ANN001
        raise ConnectionError("auth down")

    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _boom)
    pub = _publisher()

    await pub.connect(asyncio.get_running_loop())  # swallowed
    pub.publish("alice", {"x": 1})  # degraded to no-op, no raise
    await pub.close()  # connect starts the supervisor even on failure — stop it


async def test_connect_is_bounded_by_a_timeout_when_nats_hangs(monkeypatch) -> None:
    # nats-py's default connect() retries internally (up to ~2 minutes) when the
    # broker is unreachable — left unbounded, this would block FastAPI lifespan
    # startup for minutes, violating the "must not abort/delay boot" contract.
    async def _hangs(servers, **opts):  # noqa: ANN001
        await asyncio.Event().wait()  # never resolves — simulates an unreachable broker

    async def _fake_fetch(self, public_key):  # noqa: ANN001
        return "the.nats.jwt"

    monkeypatch.setattr("nats.connect", _hangs)
    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _fake_fetch)
    pub = _publisher(connect_timeout_seconds=0.05)

    start = time.monotonic()
    await asyncio.wait_for(pub.connect(asyncio.get_running_loop()), timeout=2.0)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # bounded by connect_timeout_seconds, not nats-py's own retry loop
    assert pub._nc is None  # noqa: SLF001 — degraded to snapshot-only
    pub.publish("alice", {"x": 1})  # still a no-op
    await pub.close()  # connect starts the supervisor even on failure — stop it


async def test_supervisor_force_reconnects_before_expiry_with_a_fresh_jwt(monkeypatch) -> None:
    # Steady state: the platform JWT expires in ~1 min, so the supervisor proactively
    # mints a fresh (keypair, jwt) pair and force_reconnects the LIVE connection before
    # the server can expire the current token — no auth violation, no close.
    jwts = iter(["jwt.v1", "jwt.v2", "jwt.v3", "jwt.v4", "jwt.v5"])

    async def _fake_fetch(self, public_key):  # noqa: ANN001
        return next(jwts)

    nc = _FakeNC()
    opened: dict[str, object] = {}

    async def _fake_connect(servers, **opts):  # noqa: ANN001
        opened["opts"] = opts
        return nc

    monkeypatch.setattr("nats.connect", _fake_connect)
    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _fake_fetch)
    pub = _publisher(jwt_refresh_seconds=0.01)

    await pub.connect(asyncio.get_running_loop())
    assert opened["opts"]["user_jwt_cb"]() == b"jwt.v1"  # boot-time token first

    deadline = time.monotonic() + 2.0
    while nc.force_reconnect_calls < 1 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    assert nc.force_reconnect_calls >= 1  # re-handshook the same live connection
    assert opened["opts"]["user_jwt_cb"]() != b"jwt.v1"  # ...presenting a fresh token
    await pub.close()


async def test_supervisor_rebuilds_a_brand_new_connection_when_closed(monkeypatch) -> None:
    # The core fix: when the server closes the connection permanently (auth violation
    # on JWT expiry → nats-py goes to CLOSED and never reconnects itself), the
    # supervisor rebuilds a fresh connection instead of dying until pod restart. It is
    # reason-agnostic: it only checks is_closed, not why.
    conns: list[_FakeNC] = []

    async def _fake_connect(servers, **opts):  # noqa: ANN001
        nc = _FakeNC()
        conns.append(nc)
        return nc

    async def _fake_fetch(self, public_key):  # noqa: ANN001
        return "the.nats.jwt"

    monkeypatch.setattr("nats.connect", _fake_connect)
    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _fake_fetch)
    pub = _publisher(jwt_refresh_seconds=0.01)

    await pub.connect(asyncio.get_running_loop())
    conns[0].is_closed = True  # simulate the permanent auth-violation close

    deadline = time.monotonic() + 2.0
    while len(conns) < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    assert len(conns) >= 2  # a brand-new connection was built, not force_reconnect
    assert pub._nc is conns[-1] and pub._nc.is_closed is False  # noqa: SLF001
    await pub.close()


async def test_supervisor_recovers_when_the_initial_connect_failed(monkeypatch) -> None:
    # Boot-time NATS/auth outage degrades to snapshot-only but must NOT be terminal:
    # the supervisor starts anyway and builds the connection once the outage clears.
    calls = {"n": 0}
    conns: list[_FakeNC] = []

    async def _fetch(self, public_key):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("auth down at boot")
        return "the.nats.jwt"

    async def _fake_connect(servers, **opts):  # noqa: ANN001
        nc = _FakeNC()
        conns.append(nc)
        return nc

    monkeypatch.setattr("nats.connect", _fake_connect)
    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _fetch)
    pub = _publisher(jwt_refresh_seconds=0.01)

    await pub.connect(asyncio.get_running_loop())
    assert pub._nc is None  # noqa: SLF001 — boot connect failed, snapshot-only

    deadline = time.monotonic() + 2.0
    while not conns and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    assert conns and pub._nc is conns[-1]  # noqa: SLF001 — recovered without a restart
    await pub.close()


async def test_supervisor_exchange_mints_a_fresh_keypair_and_keeps_the_pair_matched(
    monkeypatch,
) -> None:
    # Each auth exchange is a one-time key registration, so every supervisor tick mints
    # a NEW keypair and swaps (keypair, jwt) as a matched pair; a failed exchange keeps
    # the last good pair and retries next tick (fail-soft).
    seen_keys: list[str] = []
    fail_after = {"n": 0}

    async def _fetch(self, public_key):  # noqa: ANN001
        fail_after["n"] += 1
        seen_keys.append(public_key)
        if fail_after["n"] == 3:  # a mid-stream blip
            raise ConnectionError("auth down")
        return f"jwt.for.{public_key[:8]}"

    opened: dict[str, object] = {}
    nc = _FakeNC()

    async def _fake_connect(servers, **opts):  # noqa: ANN001
        opened["opts"] = opts
        return nc

    monkeypatch.setattr("nats.connect", _fake_connect)
    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _fetch)
    pub = _publisher(jwt_refresh_seconds=0.01)

    with structlog.testing.capture_logs() as logs:
        await pub.connect(asyncio.get_running_loop())
        deadline = time.monotonic() + 2.0
        while fail_after["n"] < 4 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

    assert len(set(seen_keys)) == len(seen_keys)  # every exchange used a fresh key
    # After the blip, the served (keypair, jwt) is still a matched, non-blank pair.
    current_key = pub._keypair.public_key.decode()  # noqa: SLF001
    assert opened["opts"]["user_jwt_cb"]() == f"jwt.for.{current_key[:8]}".encode()
    assert any(log.get("event") == "nats.reconnect_failed" for log in logs)
    await pub.close()


async def test_close_stops_the_supervisor(monkeypatch) -> None:
    calls = {"n": 0}

    async def _fetch(self, public_key):  # noqa: ANN001
        calls["n"] += 1
        return f"jwt.v{calls['n']}"

    async def _fake_connect(servers, **opts):  # noqa: ANN001
        return _FakeNC()

    monkeypatch.setattr("nats.connect", _fake_connect)
    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _fetch)
    pub = _publisher(jwt_refresh_seconds=0.01)

    await pub.connect(asyncio.get_running_loop())
    await pub.close()
    settled = calls["n"]
    await asyncio.sleep(0.05)
    assert calls["n"] == settled  # no further exchanges/reconnects after shutdown


async def test_connect_wires_lifecycle_callbacks(monkeypatch) -> None:
    # All four nats-py lifecycle callbacks are wired so a disconnect/close is never
    # invisible again (the prior design passed none → zero observability).
    opened: dict[str, object] = {}

    async def _fake_connect(servers, **opts):  # noqa: ANN001
        opened["opts"] = opts
        return _FakeNC()

    async def _fake_fetch(self, public_key):  # noqa: ANN001
        return "the.nats.jwt"

    monkeypatch.setattr("nats.connect", _fake_connect)
    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _fake_fetch)
    pub = _publisher()  # default cadence → supervisor won't tick during the test

    await pub.connect(asyncio.get_running_loop())
    opts = opened["opts"]
    for cb in ("error_cb", "disconnected_cb", "reconnected_cb", "closed_cb"):
        assert callable(opts[cb]), cb
    assert opts["max_reconnect_attempts"] == -1  # transient-drop cover still on
    with structlog.testing.capture_logs() as logs:
        await opts["error_cb"](RuntimeError("boom"))
        await opts["disconnected_cb"]()
        await opts["reconnected_cb"]()
        await opts["closed_cb"]()
    events = {log.get("event") for log in logs}
    assert {
        "nats.error",
        "nats.disconnected",
        "nats.reconnected",
        "nats.connection_closed",
    } <= events
    await pub.close()


async def test_publish_skips_a_known_closed_connection() -> None:
    # Between supervisor ticks a connection can be CLOSED; publish must not schedule a
    # doomed send onto the loop (which would just log ConnectionClosedError).
    pub = _publisher()
    nc = _FakeNC()
    nc.is_closed = True
    pub._nc = nc  # noqa: SLF001
    pub._loop = asyncio.get_running_loop()  # noqa: SLF001

    pub.publish("alice", {"x": 1})

    assert nc.published == []  # guard short-circuited before scheduling


def test_reconnect_interval_prefers_expires_in_over_the_fallback() -> None:
    # Cadence adapts to the platform TTL (auth response's expiresIn) when present, so a
    # TTL change doesn't need a redeploy; floored so a tiny/garbage TTL can't hot-loop.
    pub = _publisher(jwt_refresh_seconds=30.0)
    assert pub._reconnect_interval() == 30.0  # noqa: SLF001 — no expiresIn yet → fallback
    pub._token_expires_in = 100.0  # noqa: SLF001
    assert pub._reconnect_interval() == 80.0  # noqa: SLF001 — 0.8 × TTL
    pub._token_expires_in = 1.0  # noqa: SLF001
    assert pub._reconnect_interval() == 5.0  # noqa: SLF001 — floored


async def test_connect_skips_exchange_when_servers_is_whitespace_only(monkeypatch) -> None:
    # `_enabled()` is True for any non-empty string, but "   " parses to no server
    # URLs. Guard before the auth exchange so a whitespace-only misconfig fires
    # neither a wasted JWT POST nor a pointless nats.connect([]).
    async def _must_not_fetch(self, public_key):  # noqa: ANN001
        raise AssertionError("auth exchange must be skipped when no servers parse")

    async def _must_not_connect(servers, **opts):  # noqa: ANN001
        raise AssertionError("nats.connect must not run with an empty server list")

    monkeypatch.setattr(NatsSessionPublisher, "_fetch_app_jwt", _must_not_fetch)
    monkeypatch.setattr("nats.connect", _must_not_connect)
    pub = _publisher(servers="   ")

    with structlog.testing.capture_logs() as logs:
        await pub.connect(asyncio.get_running_loop())

    assert pub._nc is None  # noqa: SLF001 — degraded to snapshot-only
    assert any(log.get("error_type") == "NoServersConfigured" for log in logs)
    pub.publish("alice", {"x": 1})  # no-op, no connection opened


async def test_fetch_app_jwt_verify_certs_defaults_true_and_is_configurable(monkeypatch) -> None:
    # Default-secure, same convention as ES_VERIFY_CERTS/OIDC_VERIFY_SSL; operator
    # can opt out for a self-signed/internal auth-service CA or a broken chain.
    captured: list[dict[str, object]] = []

    class _FakeResponse:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict[str, str]:
            return {"natsToken": "jwt"}

    class _FakeClient:
        def __init__(self, **kwargs):  # noqa: ANN001
            captured.append(kwargs)

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(self, *args, **kwargs):  # noqa: ANN001
            return _FakeResponse()

    monkeypatch.setattr("ragent.clients.nats_publisher.httpx.AsyncClient", _FakeClient)

    await _publisher()._fetch_app_jwt("UABC")  # noqa: SLF001
    await _publisher(verify_certs=False)._fetch_app_jwt("UABC")  # noqa: SLF001

    assert captured[0]["verify"] is True  # default
    assert captured[1]["verify"] is False  # operator override
