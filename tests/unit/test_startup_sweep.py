"""T-ATTACH-R.1a — run_startup_sweep re-enqueues stale rows; skips fresh rows.

Verifies that:
1. PENDING rows with updated_at older than pending_stale_seconds are re-enqueued.
2. UPLOADED rows with updated_at older than uploaded_stale_seconds are re-enqueued.
3. No enqueue call is made when both lists return empty (fresh rows).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ragent.repositories.document_repository import DocumentRow


def _doc(document_id: str) -> MagicMock:
    doc = MagicMock(spec=DocumentRow)
    doc.document_id = document_id
    doc.attempt = 0
    return doc


@pytest.mark.asyncio
async def test_startup_sweep_enqueues_stale_pending_rows() -> None:
    """Stale PENDING rows must be re-enqueued."""
    repo = AsyncMock()
    repo.list_pending_stale.return_value = [_doc("DOC-P1"), _doc("DOC-P2")]
    repo.list_uploaded_stale.return_value = []
    dispatcher = AsyncMock()

    from ragent.workers.startup_sweep import run_startup_sweep

    await run_startup_sweep(
        repo=repo,
        dispatcher=dispatcher,
        pending_stale_seconds=30,
        uploaded_stale_seconds=300,
        max_attempts=5,
    )

    assert dispatcher.enqueue.call_count == 2
    calls = {c.kwargs["document_id"] for c in dispatcher.enqueue.call_args_list}
    assert calls == {"DOC-P1", "DOC-P2"}
    repo.list_pending_stale.assert_called_once()
    _, kwargs = repo.list_pending_stale.call_args
    assert kwargs["attempt_le"] == 5


@pytest.mark.asyncio
async def test_startup_sweep_enqueues_stale_uploaded_rows() -> None:
    """Stale UPLOADED rows must be re-enqueued."""
    repo = AsyncMock()
    repo.list_pending_stale.return_value = []
    repo.list_uploaded_stale.return_value = [_doc("DOC-U1")]
    dispatcher = AsyncMock()

    from ragent.workers.startup_sweep import run_startup_sweep

    await run_startup_sweep(
        repo=repo,
        dispatcher=dispatcher,
        pending_stale_seconds=30,
        uploaded_stale_seconds=300,
    )

    dispatcher.enqueue.assert_called_once_with("ingest.pipeline", document_id="DOC-U1")


@pytest.mark.asyncio
async def test_startup_sweep_no_enqueue_for_fresh_rows() -> None:
    """No enqueue call when both lists return empty."""
    repo = AsyncMock()
    repo.list_pending_stale.return_value = []
    repo.list_uploaded_stale.return_value = []
    dispatcher = AsyncMock()

    from ragent.workers.startup_sweep import run_startup_sweep

    await run_startup_sweep(
        repo=repo,
        dispatcher=dispatcher,
        pending_stale_seconds=30,
        uploaded_stale_seconds=300,
    )

    dispatcher.enqueue.assert_not_called()
