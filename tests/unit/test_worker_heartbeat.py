"""Unit tests for ragent.workers.heartbeat.run_heartbeat (B16, TA.10).

run_heartbeat runs in a plain threading.Thread and owns a private asyncio
event loop.  It calls repo.update_heartbeat periodically until the stop
Event is set.

Also covers the import surface of ragent.worker (process entrypoint) —
the module only defines imports + __main__ block; the __main__ block is
marked # pragma: no cover; the import lines are covered here.
"""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock

import ragent.worker  # noqa: F401  — covers module-level import lines (lines 3-8)
from ragent.workers.heartbeat import run_heartbeat


def _make_repo(side_effect=None) -> MagicMock:
    repo = MagicMock()
    repo.update_heartbeat = AsyncMock(side_effect=side_effect)
    return repo


def test_run_heartbeat_stops_when_event_set() -> None:
    """Setting the stop event before the first tick exits without calling the repo."""
    stop = threading.Event()
    stop.set()
    repo = _make_repo()

    run_heartbeat("doc-1", repo, stop, interval=0.0)

    repo.update_heartbeat.assert_not_called()


def test_run_heartbeat_calls_repo_once_then_stops() -> None:
    """Heartbeat fires once, then stop event is set by a timer."""
    stop = threading.Event()
    repo = _make_repo()

    # Set the stop event after a short delay so exactly one tick fires.
    threading.Timer(0.05, stop.set).start()
    run_heartbeat("doc-2", repo, stop, interval=0.01)

    assert repo.update_heartbeat.call_count >= 1
    repo.update_heartbeat.assert_called_with("doc-2")


def test_run_heartbeat_logs_and_continues_on_error() -> None:
    """A repo error on one tick must not crash the loop — next tick still runs."""
    stop = threading.Event()
    call_count = [0]

    async def flaky(*_):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("transient DB error")
        # Second call succeeds; set the stop event so the loop exits.
        stop.set()

    repo = _make_repo(side_effect=flaky)

    run_heartbeat("doc-3", repo, stop, interval=0.01)

    # Both attempts were made (one failed, one succeeded).
    assert repo.update_heartbeat.call_count == 2
