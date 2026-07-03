"""T-CAv3N — NATS publisher for live session-list status (app-flow auth).

Mirrors mco-clean's NATS connection (ephemeral Ed25519 nkey → JWT exchange →
challenge-response on connect), but uses the **backend "app" auth flow** instead
of the frontend's per-user "tsso" flow:

  1. Generate an ephemeral Ed25519 user nkey (public key + seed), kept in memory.
  2. POST the NATS auth service with the app's `client_secret` + `namespace` + the
     public key → receive a short-lived NATS user JWT (`natsToken`).
  3. Connect to NATS presenting that JWT and signing the server nonce with the
     ephemeral seed (`user_jwt_cb` + `signature_cb`, exactly as nats-py signs for
     `nkeys_seed`: `base64.b64encode(seed.sign(nonce))`).

ragent publishes a run's start/finish (and a session-open dot-clear) to a per-user
subject the frontend subscribes to. Everything is best-effort / fail-soft: an auth
or connect failure degrades to "snapshot only" (the durable truth is the Redis
run-pointer + unread flag the client re-reads from `sessionList`) and never aborts
boot or fails an HTTP request.

**Connection supervisor.** The platform's app JWTs are short-lived (~1 minute) and
the server disconnects a connection when its JWT expires — with a protocol
``-ERR 'Authorization Violation'`` that nats-py handles by closing the connection
*permanently* (straight to CLOSED, bypassing its own reconnect loop, regardless of
``max_reconnect_attempts``). So keeping the token fresh in place is not enough: once
the JWT expires the channel would die until pod restart. Instead a background
supervisor (:meth:`_maintain_connection`) owns the connection lifecycle,
reason-agnostically:

  * **Proactive** — every ``_reconnect_interval`` (< the JWT TTL, derived from the
    auth response's ``expiresIn`` when present) it mints a fresh (keypair, jwt) pair
    and ``force_reconnect``\\s, so the server always sees a live token and never
    sends the auth violation that would close the connection.
  * **Self-healing** — if the connection is nonetheless found CLOSED (an auth-service
    blip let the token expire, or any other permanent close), it rebuilds a brand-new
    connection. It never inspects *why* the connection died: dead → reconnect.

The only close it does not fight is our own shutdown (:meth:`close` sets ``_closing``).
nats-py's own reconnect (``max_reconnect_attempts=-1``) still covers transient network
drops between supervisor ticks; the lifecycle callbacks log every disconnect/close so
the reason is never invisible again.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
from concurrent.futures import Future
from typing import Any

import httpx
import nkeys
import structlog

logger = structlog.get_logger(__name__)

_AUTH_PATH = "/api/v1/auth"
# Reconnect this far into the token's lifetime — early enough to re-handshake with a
# fresh JWT before the server expires the current one, late enough to avoid churn.
_RECONNECT_TTL_FRACTION = 0.8
# Floor so a tiny/misconfigured expiresIn can't spin the supervisor into a hot loop.
_MIN_RECONNECT_INTERVAL = 5.0


def _new_user_keypair() -> Any:
    """An ephemeral Ed25519 user nkey — public key (`U…`) + seed (`SU…`), in memory."""
    seed = nkeys.encode_seed(os.urandom(32), nkeys.PREFIX_BYTE_USER)
    return nkeys.from_seed(seed)


class NatsSessionPublisher:
    """Publishes per-user session-list status events to the shared platform NATS.

    Constructed (with config) in the composition root and connected later in the
    lifespan via :meth:`connect`. When unconfigured — or before connect, or after a
    failed exchange/connect — :meth:`publish` is a no-op, so the live channel degrades
    to "snapshot only" rather than failing a request.

    ``verify_certs`` (default ``True``, same default-secure convention as
    ``ES_VERIFY_CERTS``/``OIDC_VERIFY_SSL``) gates TLS verification on the
    ``_fetch_app_jwt`` POST only — it has no effect on the later NATS connection.
    Set ``False`` only for a dev/self-signed auth-service CA or a broken
    intermediate chain; that POST carries ``client_secret``, so this must stay
    ``True`` anywhere the connection isn't already trusted.
    """

    def __init__(
        self,
        *,
        servers: str | None,
        auth_service_url: str | None,
        client_secret: str | None,
        namespace: str | None,
        subject_template: str = "session.{user}.status",
        verify_certs: bool = True,
        connect_timeout_seconds: float = 10.0,
        jwt_refresh_seconds: float = 30.0,
    ) -> None:
        self._servers = servers
        self._auth_url = auth_service_url.rstrip("/") if auth_service_url else None
        self._client_secret = client_secret
        self._namespace = namespace
        self._subject_template = subject_template
        self._verify_certs = verify_certs
        # nats-py's connect() retries internally (default up to ~2 min) when
        # unreachable; bound the initial attempt so a NATS outage can't stall
        # FastAPI lifespan startup.
        self._connect_timeout_seconds = connect_timeout_seconds
        # Fallback reconnect cadence when the auth response omits `expiresIn`. Must
        # stay under the platform JWT's TTL (~60s) so a re-handshake always presents
        # a live token before the server expires the current one.
        self._jwt_refresh_seconds = jwt_refresh_seconds
        self._nc: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._keypair: Any = None
        self._jwt: str | None = None
        self._token_expires_in: float | None = None
        self._server_list: list[str] = []
        self._maintain_task: asyncio.Task[None] | None = None
        self._closing = False

    def _enabled(self) -> bool:
        return bool(self._servers and self._auth_url and self._client_secret and self._namespace)

    def subject(self, user_id: str) -> str:
        # Operator-configurable via NATS_SESSION_SUBJECT_TEMPLATE; per-user so a
        # subscriber only ever receives their own runs' transitions.
        return self._subject_template.format(user=user_id)

    def _auth_payload(self, public_key: str) -> dict[str, str]:
        # `publicKey` is camelCase to mirror the frontend's nats-auth.ts payload key.
        return {
            "token_type": "app",
            "token": self._client_secret or "",
            "namespace": self._namespace or "",
            "publicKey": public_key,
        }

    async def _fetch_app_jwt(self, public_key: str) -> str:
        async with httpx.AsyncClient(verify=self._verify_certs) as client:
            resp = await client.post(
                f"{self._auth_url}{_AUTH_PATH}",
                json=self._auth_payload(public_key),
                timeout=10.0,
            )
            resp.raise_for_status()
            body = resp.json()
            # Drives the proactive reconnect cadence; absent/garbage → fall back to the
            # configured interval (see _reconnect_interval). Coerce defensively: the auth
            # service is external, and a non-numeric expiresIn (e.g. the string "60") must
            # not reach _reconnect_interval's `> 0` compare — that runs OUTSIDE the
            # supervisor's try/except, so a TypeError there would kill it permanently.
            raw_expires = body.get("expiresIn")
            try:
                self._token_expires_in = None if raw_expires is None else float(raw_expires)
            except (TypeError, ValueError):
                self._token_expires_in = None
            return body["natsToken"]

    async def _refresh_credentials(self) -> None:
        """Mint a fresh ephemeral keypair and exchange it for a new JWT.

        Each auth-service exchange is a one-time key registration (re-POSTing an
        already-registered publicKey is rejected), so every refresh uses a NEW
        keypair — mirroring the frontend's per-connection flow. The (keypair, jwt)
        pair is swapped with no await between the assignments, and nats-py's
        handshake reads both callbacks synchronously, so a reconnect can never
        observe a mixed (old key, new token) pair.
        """
        keypair = _new_user_keypair()
        jwt = await self._fetch_app_jwt(keypair.public_key.decode())
        self._keypair = keypair
        self._jwt = jwt

    async def _open_connection(self) -> None:
        """(Re)establish the NATS connection using the current (keypair, jwt) pair.

        Bounded by ``connect_timeout_seconds`` so an unreachable broker can't stall
        the caller. Lifecycle callbacks log every transition; the actual rebuild on
        a permanent close is driven by the supervisor, not the callbacks.
        """
        import nats

        self._nc = await asyncio.wait_for(
            nats.connect(
                self._server_list,
                name="ragent",
                # Read the instance attrs, not a snapshot: nats-py re-invokes these
                # on every (re)connect handshake, so a reconnect presents the
                # supervisor's latest freshly-minted pair.
                user_jwt_cb=lambda: (self._jwt or "").encode(),
                signature_cb=lambda nonce: base64.b64encode(self._keypair.sign(nonce.encode())),
                # Covers transient network drops between supervisor ticks; the
                # default gives up permanently after ~2 min, wrong for a weeks-lived
                # pod. (Does NOT cover auth-violation closes — the supervisor does.)
                max_reconnect_attempts=-1,
                error_cb=self._on_error,
                disconnected_cb=self._on_disconnected,
                reconnected_cb=self._on_reconnected,
                closed_cb=self._on_closed,
            ),
            timeout=self._connect_timeout_seconds,
        )

    async def connect(self, loop: asyncio.AbstractEventLoop) -> None:
        """Open the connection and start the supervisor (lifespan startup).

        No-op when unconfigured. The initial connect is fail-soft (a failure is
        logged, boot continues snapshot-only), but the supervisor still starts and
        keeps retrying so a NATS/auth outage at boot self-heals when it clears.
        """
        if not self._enabled():
            return
        # `_enabled()` only checks the raw string is truthy; a whitespace-only
        # NATS_SERVERS parses to no URLs — bail before the supervisor/JWT POST.
        servers = [s.strip() for s in self._servers.split(",") if s.strip()]  # type: ignore[union-attr]
        if not servers:
            logger.warning("nats.connect_failed", error_type="NoServersConfigured")
            return
        self._server_list = servers
        self._loop = loop
        try:
            await self._refresh_credentials()
            await self._open_connection()
            logger.info("nats.connected")
        except Exception as exc:  # noqa: BLE001 — best-effort channel; degrade to snapshot-only
            logger.warning("nats.connect_failed", error_type=type(exc).__name__)
            self._nc = None
        self._maintain_task = loop.create_task(self._maintain_connection())

    def _reconnect_interval(self) -> float:
        """Seconds until the next proactive reconnect — a fraction of the token TTL.

        Uses the auth response's ``expiresIn`` when available (so it adapts if the
        platform changes the TTL), else the configured fallback; floored so a
        misconfigured tiny TTL can't spin a hot loop.
        """
        if self._token_expires_in and self._token_expires_in > 0:
            return max(_MIN_RECONNECT_INTERVAL, self._token_expires_in * _RECONNECT_TTL_FRACTION)
        return max(_MIN_RECONNECT_INTERVAL, self._jwt_refresh_seconds)

    async def _maintain_connection(self) -> None:
        """Own the connection lifecycle: reconnect on any death, before every expiry.

        Reason-agnostic by design — it never inspects *why* the connection is gone:
        if CLOSED, rebuild; otherwise re-handshake with a fresh token before the
        server expires the current one. A failed tick keeps the last good pair and
        retries next tick (fail-soft). The only close it does not fight is our own
        shutdown (``_closing``).
        """
        while not self._closing:
            await asyncio.sleep(self._reconnect_interval())
            if self._closing:
                return
            try:
                await self._refresh_credentials()
                if self._nc is None or self._nc.is_closed:
                    await self._open_connection()
                else:
                    await self._nc.force_reconnect()
            except Exception as exc:  # noqa: BLE001 — keep last good pair, retry next tick
                logger.warning("nats.reconnect_failed", error_type=type(exc).__name__)

    async def _on_error(self, exc: Exception) -> None:
        logger.warning("nats.error", error_type=type(exc).__name__)

    async def _on_disconnected(self) -> None:
        logger.warning("nats.disconnected")

    async def _on_reconnected(self) -> None:
        logger.info("nats.reconnected")

    async def _on_closed(self) -> None:
        # The supervisor rebuilds on its next tick; this is for visibility only.
        logger.warning("nats.connection_closed")

    def publish(self, user_id: str, event: dict[str, Any]) -> None:
        """Fire-and-forget publish, callable from any thread (incl. the producer pool).

        Schedules the async publish on the app loop and does NOT wait. Fail-soft: a
        serialization error, a closed loop, or a dropped connection only costs this
        one live nudge — the client re-syncs from the sessionList snapshot. A failure
        inside the scheduled coroutine itself (e.g. the connection drops mid-send)
        lands on the returned Future, not this method's own try/except, so it needs
        its own done-callback to avoid vanishing with no log at all.
        """
        if self._nc is None or self._loop is None or getattr(self._nc, "is_closed", False):
            return
        try:
            data = json.dumps(event).encode()
            subject = self.subject(user_id)
            future = asyncio.run_coroutine_threadsafe(self._nc.publish(subject, data), self._loop)
            future.add_done_callback(self._log_publish_failure)
        except Exception as exc:  # noqa: BLE001 — best-effort live nudge
            logger.warning("nats.session_publish_failed", error_type=type(exc).__name__)

    @staticmethod
    def _log_publish_failure(future: Future[None]) -> None:
        # A cancelled future (e.g. an outstanding task cancelled during event-loop
        # shutdown) makes .exception() raise CancelledError rather than return it.
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.warning("nats.session_publish_failed", error_type=type(exc).__name__)

    async def close(self) -> None:
        """Stop the supervisor and drain the connection on lifespan shutdown.

        Sets ``_closing`` first so the supervisor treats this as a deliberate stop,
        not a disconnect to reconnect from.
        """
        self._closing = True
        if self._maintain_task is not None:
            # Await the cancellation so the supervisor is fully stopped before we
            # drain — otherwise a tick mid-`force_reconnect` could race the drain.
            self._maintain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._maintain_task
            self._maintain_task = None
        if self._nc is None:
            return
        try:
            await self._nc.drain()
        except Exception:  # noqa: BLE001 — shutdown path; log and continue
            logger.warning("nats.close_failed", exc_info=True)
        finally:
            self._nc = None
