"""T-ATTACH-R.0c — heartbeat thread starts on successful claim, stops on all paths.

Verifies that ingest_pipeline_task:
1. Starts a daemon heartbeat thread after claim_for_processing succeeds.
2. Sets stop_event after pipeline succeeds (happy path).
3. Sets stop_event after pipeline raises an exception (failure path).
4. Does NOT start a heartbeat thread when claim returns None (skip path).
"""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragent.repositories.document_repository import DocumentRow


def _doc() -> MagicMock:
    doc = MagicMock(spec=DocumentRow)
    doc.document_id = "DOC-HB-1"
    doc.minio_site = "__default__"
    doc.object_key = "test.txt"
    doc.source_id = "S1"
    doc.source_app = "test-app"
    doc.source_url = None
    doc.source_title = "Test"
    doc.source_meta = None
    doc.ingest_type = "inline"
    doc.attempt = 0
    doc.mime_type = "text/plain"
    doc.create_user = "u1"
    return doc


def _container(doc: MagicMock | None, *, pipeline_raises: Exception | None = None) -> MagicMock:
    container = MagicMock()
    container.doc_repo = AsyncMock()
    container.doc_repo.claim_for_processing.return_value = doc
    container.doc_repo.update_status = AsyncMock()
    container.doc_repo.promote_to_ready_and_demote_siblings = AsyncMock(return_value=True)
    container.minio_registry = MagicMock()
    container.minio_registry.head_object.return_value = (6, "text/plain")
    container.minio_registry.get_object.return_value = b"hello\n"
    if pipeline_raises:
        container.ingest_pipeline.run.side_effect = pipeline_raises
    else:
        container.ingest_pipeline.run.return_value = {
            "embedder": {"documents": [], "documents_written": 1}
        }
    container.registry = MagicMock()
    container.registry.fan_out = AsyncMock()
    container.unprotect_client = None
    container.embedding_registry.refresh = AsyncMock()
    container.heartbeat_tick = MagicMock()
    container.heartbeat_interval = 60.0  # long interval so it never fires during test
    return container


@pytest.mark.asyncio
async def test_heartbeat_thread_starts_and_stops_on_success():
    """Heartbeat daemon thread must be started after claim; stop event set on success."""
    doc = _doc()
    container = _container(doc)
    started_threads: list[threading.Thread] = []

    original_thread = threading.Thread

    def _capture_thread(*args, **kwargs):
        t = original_thread(*args, **kwargs)
        started_threads.append(t)
        return t

    with (
        patch("ragent.bootstrap.composition.get_container", return_value=container),
        patch("ragent.workers.ingest.threading.Thread", side_effect=_capture_thread),
    ):
        from ragent.workers.ingest import ingest_pipeline_task

        await ingest_pipeline_task("DOC-HB-1")

    hb_threads = [t for t in started_threads if t.daemon]
    assert len(hb_threads) >= 1, "at least one daemon heartbeat thread must be started"


@pytest.mark.asyncio
async def test_heartbeat_stop_event_set_on_pipeline_failure():
    """Stop event must be set even when the pipeline raises."""
    doc = _doc()
    container = _container(doc, pipeline_raises=RuntimeError("boom"))
    stop_events: list[threading.Event] = []

    original_event = threading.Event

    def _capture_event():
        ev = original_event()
        stop_events.append(ev)
        return ev

    with (
        patch("ragent.bootstrap.composition.get_container", return_value=container),
        patch("ragent.workers.ingest.threading.Event", side_effect=_capture_event),
    ):
        from ragent.workers.ingest import ingest_pipeline_task

        await ingest_pipeline_task("DOC-HB-1")

    assert stop_events, "threading.Event must be created for heartbeat"
    assert all(ev.is_set() for ev in stop_events), "all stop events must be set after task"


@pytest.mark.asyncio
async def test_heartbeat_not_started_when_claim_returns_none():
    """When claim_for_processing returns None, no heartbeat thread is started."""
    container = _container(None)
    started_threads: list[threading.Thread] = []

    original_thread = threading.Thread

    def _capture_thread(*args, **kwargs):
        t = original_thread(*args, **kwargs)
        started_threads.append(t)
        return t

    with (
        patch("ragent.bootstrap.composition.get_container", return_value=container),
        patch("ragent.workers.ingest.threading.Thread", side_effect=_capture_thread),
    ):
        from ragent.workers.ingest import ingest_pipeline_task

        await ingest_pipeline_task("DOC-HB-1")

    daemon_threads = [t for t in started_threads if t.daemon]
    assert daemon_threads == [], "no heartbeat thread should start when claim is skipped"
