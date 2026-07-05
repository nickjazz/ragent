"""T-ATTACH-R.1b / T-ATTACH-R.3c — WORKER_STARTUP handler: startup sweep + maintenance loop."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_worker_startup_handler_invokes_sweep_with_container_thresholds() -> None:
    """WORKER_STARTUP handler must call run_startup_sweep with container thresholds."""
    from ragent.bootstrap.composition import Container

    container = MagicMock(spec=Container)
    container.doc_repo = AsyncMock()
    container.dispatcher = AsyncMock()
    container.pending_stale_seconds = 30
    container.uploaded_stale_seconds = 300
    container.max_attempts = 5

    with (
        patch("ragent.bootstrap.composition.get_container", return_value=container),
        patch("ragent.workers.ingest.run_startup_sweep", new_callable=AsyncMock) as mock_sweep,
    ):
        from ragent.workers.ingest import _on_worker_startup

        await _on_worker_startup(MagicMock())

    mock_sweep.assert_called_once_with(
        repo=container.doc_repo,
        dispatcher=container.dispatcher,
        pending_stale_seconds=30,
        uploaded_stale_seconds=300,
        max_attempts=5,
    )


@pytest.mark.asyncio
async def test_worker_startup_handler_creates_maintenance_loop_task() -> None:
    """WORKER_STARTUP handler must create an asyncio task for the maintenance loop."""
    from ragent.bootstrap.composition import Container

    container = MagicMock(spec=Container)
    container.doc_repo = AsyncMock()
    container.dispatcher = AsyncMock()
    container.pending_stale_seconds = 30
    container.uploaded_stale_seconds = 300
    container.max_attempts = 5
    container.maintenance_interval_seconds = 300
    container.deleting_stale_seconds = 300

    created_coros = []

    def capture_task(coro):
        created_coros.append(coro)
        coro.close()
        return MagicMock()

    with (
        patch("ragent.bootstrap.composition.get_container", return_value=container),
        patch("ragent.workers.ingest.run_startup_sweep", new_callable=AsyncMock),
        patch("asyncio.create_task", side_effect=capture_task),
    ):
        from ragent.workers.ingest import _on_worker_startup

        await _on_worker_startup(MagicMock())

    assert len(created_coros) == 1, "expected exactly one asyncio.create_task call"


@pytest.mark.asyncio
async def test_worker_startup_handler_is_registered_on_broker() -> None:
    """The WORKER_STARTUP handler must be registered on the broker."""
    from taskiq import TaskiqEvents

    from ragent.bootstrap.broker import broker
    from ragent.workers.ingest import _on_worker_startup

    handlers = broker.event_handlers.get(TaskiqEvents.WORKER_STARTUP, [])
    assert _on_worker_startup in handlers, (
        "_on_worker_startup must be registered as a WORKER_STARTUP handler"
    )
