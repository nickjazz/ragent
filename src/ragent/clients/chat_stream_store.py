"""T-CAv3R — Redis Stream buffer that makes a /chatagent/v3 run resumable.

A run's SSE frames are tee'd into a Redis Stream keyed by
``chatstream:{user_id}:{thread_id}:{run_id}`` by a background producer that is
decoupled from the client connection, so generation completes even if the
client refreshes. A consumer (the original POST response or a later
``GET /chatagent/v3/reconnect``) replays the stream from a cursor — exclusive of
the client's ``Last-Event-ID`` — so a reconnect resumes exactly where the drop
happened. The key carries the owner so a run cannot be reconnected by guessing
its run_id, and a short TTL bounds how long a finished run stays resumable.

Reads use ``XRANGE`` (not blocking ``XREAD``) so the consumer polls — this keeps
the cursor logic identical across the live POST stream and a cross-pod reconnect
(the producer may run on a different replica), and avoids relying on blocking
semantics that the in-memory test double does not honour.
"""

from __future__ import annotations

import os
from typing import Any

import redis as redis_lib

_KEY_PREFIX = "chatstream:"
_FROM_START = ("0", "-", "", None)
_FIELD_FRAME = "frame"  # XADD field holding one SSE frame string
_FIELD_EOS = "eos"  # XADD field marking the terminal sentinel (no frame)


class ChatStreamStore:
    def __init__(self, redis_client: Any, *, ttl_seconds: int = 300, maxlen: int = 10_000) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._maxlen = maxlen

    @staticmethod
    def key(user_id: str, thread_id: str, run_id: str) -> str:
        return f"{_KEY_PREFIX}{user_id}:{thread_id}:{run_id}"

    def try_start(self, key: str) -> bool:
        """Acquire the single-producer lock for a run; True only for the first caller."""
        return bool(self._redis.set(f"{key}:lock", "1", nx=True, ex=self._ttl))

    def append(self, key: str, frame: str) -> str:
        """Buffer one SSE frame; returns the entry id used as the SSE ``id:``."""
        return self._redis.xadd(key, {_FIELD_FRAME: frame}, maxlen=self._maxlen, approximate=True)

    def mark_done(self, key: str) -> None:
        """Close the stream: an ``eos`` sentinel tells consumers to stop, then bound the TTL."""
        pipe = self._redis.pipeline()
        pipe.xadd(key, {_FIELD_EOS: "1"}, maxlen=self._maxlen, approximate=True)
        pipe.expire(key, self._ttl)
        pipe.execute()

    def exists(self, key: str) -> bool:
        return bool(self._redis.exists(key))

    def read_after(self, key: str, last_id: str | None) -> list[tuple[str, str | None]]:
        """Entries strictly after ``last_id`` as ``(entry_id, frame)`` pairs.

        ``frame`` is ``None`` for the terminal ``eos`` sentinel — so consumers
        never touch the Redis field names. ``last_id`` is exclusive (Last-Event-ID
        semantics): xrange min is inclusive, so the cursor entry itself is dropped.
        """
        if last_id in _FROM_START:
            entries = self._redis.xrange(key, min="-", max="+")
        else:
            entries = [e for e in self._redis.xrange(key, min=last_id, max="+") if e[0] != last_id]
        return [
            (eid, fields.get(_FIELD_FRAME) if _FIELD_EOS not in fields else None)
            for eid, fields in entries
        ]

    @classmethod
    def from_env(cls) -> ChatStreamStore:
        ttl = int(os.environ.get("REDIS_STREAM_TTL_SECONDS", "300"))
        maxlen = int(os.environ.get("REDIS_STREAM_MAXLEN", "10000"))
        mode = os.environ.get("REDIS_MODE", "standalone")
        if mode == "sentinel":
            from redis.sentinel import Sentinel

            hosts_raw = os.environ.get("REDIS_SENTINEL_HOSTS", "")
            master = os.environ.get("REDIS_STREAM_SENTINEL_MASTER", "stream-master")
            sentinels = [
                (h.rsplit(":", 1)[0], int(h.rsplit(":", 1)[1]))
                for h in hosts_raw.split(",")
                if h.strip()
            ]
            sentinel = Sentinel(sentinels)
            client = sentinel.master_for(master, decode_responses=True)
            return cls(client, ttl_seconds=ttl, maxlen=maxlen)

        url = os.environ.get("REDIS_STREAM_URL", "redis://localhost:6379/2")
        return cls(redis_lib.from_url(url, decode_responses=True), ttl_seconds=ttl, maxlen=maxlen)
