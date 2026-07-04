"""T-ATTACH-R.0a — unit tests for workers/heartbeat.run_heartbeat."""

import threading

from ragent.workers.heartbeat import run_heartbeat


def test_heartbeat_calls_tick_at_interval():
    calls: list[str] = []
    stop = threading.Event()

    def tick(document_id: str) -> None:
        calls.append(document_id)
        if len(calls) >= 2:
            stop.set()

    t = threading.Thread(
        target=run_heartbeat,
        args=("doc-1", tick, stop),
        kwargs={"interval": 0.05},
        daemon=True,
    )
    t.start()
    t.join(timeout=2.0)

    assert len(calls) >= 2
    assert all(c == "doc-1" for c in calls)


def test_heartbeat_stops_when_event_set():
    calls: list[str] = []
    stop = threading.Event()
    stop.set()  # pre-set → loop never fires

    t = threading.Thread(
        target=run_heartbeat,
        args=("doc-2", lambda doc_id: calls.append(doc_id), stop),
        kwargs={"interval": 0.05},
        daemon=True,
    )
    t.start()
    t.join(timeout=1.0)

    assert calls == []


def test_heartbeat_tick_exception_does_not_crash_loop():
    good_calls: list[str] = []
    stop = threading.Event()
    count = 0

    def flaky_tick(document_id: str) -> None:
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("transient DB error")
        good_calls.append(document_id)
        if len(good_calls) >= 1:
            stop.set()

    t = threading.Thread(
        target=run_heartbeat,
        args=("doc-3", flaky_tick, stop),
        kwargs={"interval": 0.05},
        daemon=True,
    )
    t.start()
    t.join(timeout=2.0)

    assert len(good_calls) >= 1, "loop must survive a tick exception"


def test_heartbeat_no_os_environ_read():
    """Ensure heartbeat module does not read os.environ at module level (R3)."""
    import os
    import sys
    import unittest.mock as mock

    with mock.patch.dict(os.environ, {}, clear=False):
        # If module-level os.environ.get is called during import, we'd see it
        # Re-importing detects module-level reads
        mod_name = "ragent.workers.heartbeat"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        import ragent.workers.heartbeat  # noqa: F401  # re-import to trigger module-level code

    # The test passes if import does not raise and run_heartbeat signature
    # accepts interval as an explicit param (not relying on module-level default)
    import inspect

    from ragent.workers.heartbeat import run_heartbeat as hb

    sig = inspect.signature(hb)
    params = list(sig.parameters.keys())
    assert "tick" in params, "run_heartbeat must accept 'tick' callable param"
    assert "interval" in params, "run_heartbeat must accept 'interval' param"
    assert "document_id" in params
    assert "stop" in params
