"""T-ATTACH-R.3c — run_maintenance_cycle: mark exceeded FAILED, resume DELETING, redispatch."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragent.errors.codes import TaskErrorCode
from ragent.repositories.document_repository import DocumentRow

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _doc(**kwargs) -> MagicMock:
    d = MagicMock(spec=DocumentRow)
    d.document_id = kwargs.get("document_id", "DOC-MAINT-1")
    d.source_app = "test-app"
    d.mime_type = "text/plain"
    d.attempt = kwargs.get("attempt", 6)
    return d


def _make_deps(
    exceeded=None,
    stale_deleting=None,
    stale_pending=None,
    stale_uploaded=None,
):
    repo = AsyncMock()
    repo.list_pending_exceeded.return_value = exceeded or []
    repo.list_deleting_stale.return_value = stale_deleting or []
    repo.list_pending_stale.return_value = stale_pending or []
    repo.list_uploaded_stale.return_value = stale_uploaded or []
    repo.update_status = AsyncMock()
    repo.delete = AsyncMock()

    registry = MagicMock()
    registry.fan_out_delete = AsyncMock()

    dispatcher = AsyncMock()
    return repo, registry, dispatcher


# ---------------------------------------------------------------------------
# mark exceeded PENDING as FAILED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_exceeded_updates_status_to_failed():
    doc = _doc(attempt=6)
    repo, registry, dispatcher = _make_deps(exceeded=[doc])

    from ragent.workers.maintenance import run_maintenance_cycle

    await run_maintenance_cycle(
        repo=repo,
        registry=registry,
        dispatcher=dispatcher,
        pending_stale_seconds=30,
        uploaded_stale_seconds=300,
        deleting_stale_seconds=300,
        max_attempts=5,
    )

    call = repo.update_status.call_args
    assert call.args[0] == doc.document_id
    assert call.kwargs["from_status"] == "PENDING"
    assert call.kwargs["to_status"] == "FAILED"
    assert call.kwargs["error_code"] == TaskErrorCode.PIPELINE_MAX_ATTEMPTS_EXCEEDED


@pytest.mark.asyncio
async def test_mark_exceeded_calls_fan_out_delete():
    doc = _doc(attempt=6)
    repo, registry, dispatcher = _make_deps(exceeded=[doc])

    from ragent.workers.maintenance import run_maintenance_cycle

    await run_maintenance_cycle(
        repo=repo,
        registry=registry,
        dispatcher=dispatcher,
        pending_stale_seconds=30,
        uploaded_stale_seconds=300,
        deleting_stale_seconds=300,
        max_attempts=5,
    )

    registry.fan_out_delete.assert_awaited_once_with(doc.document_id)


# ---------------------------------------------------------------------------
# resume stale DELETING
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_deleting_calls_fan_out_delete_and_repo_delete():
    doc = _doc(document_id="DOC-DEL-1", attempt=0)
    repo, registry, dispatcher = _make_deps(stale_deleting=[doc])

    from ragent.workers.maintenance import run_maintenance_cycle

    await run_maintenance_cycle(
        repo=repo,
        registry=registry,
        dispatcher=dispatcher,
        pending_stale_seconds=30,
        uploaded_stale_seconds=300,
        deleting_stale_seconds=300,
        max_attempts=5,
    )

    registry.fan_out_delete.assert_awaited_once_with(doc.document_id)
    repo.delete.assert_awaited_once_with(doc.document_id)


# ---------------------------------------------------------------------------
# redispatch stale PENDING and UPLOADED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redispatch_stale_pending_enqueues():
    doc = _doc(document_id="DOC-PEND-1", attempt=2)
    repo, registry, dispatcher = _make_deps(stale_pending=[doc])

    from ragent.workers.maintenance import run_maintenance_cycle

    await run_maintenance_cycle(
        repo=repo,
        registry=registry,
        dispatcher=dispatcher,
        pending_stale_seconds=30,
        uploaded_stale_seconds=300,
        deleting_stale_seconds=300,
        max_attempts=5,
    )

    dispatcher.enqueue.assert_awaited_once_with("ingest.pipeline", document_id=doc.document_id)


@pytest.mark.asyncio
async def test_redispatch_stale_uploaded_enqueues():
    doc = _doc(document_id="DOC-UP-1", attempt=0)
    repo, registry, dispatcher = _make_deps(stale_uploaded=[doc])

    from ragent.workers.maintenance import run_maintenance_cycle

    await run_maintenance_cycle(
        repo=repo,
        registry=registry,
        dispatcher=dispatcher,
        pending_stale_seconds=30,
        uploaded_stale_seconds=300,
        deleting_stale_seconds=300,
        max_attempts=5,
    )

    dispatcher.enqueue.assert_awaited_once_with("ingest.pipeline", document_id=doc.document_id)
