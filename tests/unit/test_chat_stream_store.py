"""T-CAv3R.1 — ChatStreamStore: Redis Stream buffer for resumable v3 runs."""

from __future__ import annotations

import fakeredis

from ragent.clients.chat_stream_store import ChatStreamStore


def _store(**kwargs) -> ChatStreamStore:
    return ChatStreamStore(fakeredis.FakeStrictRedis(decode_responses=True), **kwargs)


def test_key_is_owner_scoped() -> None:
    # The owner (user_id) is part of the key so another user cannot reconnect to
    # a run by guessing its run_id.
    key = ChatStreamStore.key("alice", "thread_1", "run_1")
    assert key == "chatstream:alice:thread_1:run_1"
    assert ChatStreamStore.key("bob", "thread_1", "run_1") != key


def test_append_then_read_after_from_start_returns_frames_in_order() -> None:
    store = _store()
    key = ChatStreamStore.key("alice", "t", "r")
    id1 = store.append(key, "data: a\n\n")
    id2 = store.append(key, "data: b\n\n")

    entries = store.read_after(key, "0")
    assert entries == [(id1, "data: a\n\n"), (id2, "data: b\n\n")]


def test_read_after_is_exclusive_of_last_id() -> None:
    # Resume semantics: a client that already saw `id1` must only get frames
    # strictly after it (Last-Event-ID is exclusive).
    store = _store()
    key = ChatStreamStore.key("alice", "t", "r")
    id1 = store.append(key, "data: a\n\n")
    id2 = store.append(key, "data: b\n\n")

    entries = store.read_after(key, id1)
    assert [eid for eid, _ in entries] == [id2]


def test_mark_done_appends_eos_sentinel_and_sets_ttl() -> None:
    store = _store(ttl_seconds=300)
    key = ChatStreamStore.key("alice", "t", "r")
    store.append(key, "data: a\n\n")
    store.mark_done(key)

    entries = store.read_after(key, "0")
    assert entries[-1][1] is None  # terminal sentinel surfaces as a None frame
    assert store._redis.ttl(key) > 0  # noqa: SLF001 — asserting the TTL was set


def test_exists_false_before_first_append_true_after() -> None:
    store = _store()
    key = ChatStreamStore.key("alice", "t", "r")
    assert store.exists(key) is False
    store.append(key, "data: a\n\n")
    assert store.exists(key) is True


def test_try_start_is_idempotent_first_caller_wins() -> None:
    # Two POSTs with the same run_id must only spawn one producer.
    store = _store()
    key = ChatStreamStore.key("alice", "t", "r")
    assert store.try_start(key) is True
    assert store.try_start(key) is False
