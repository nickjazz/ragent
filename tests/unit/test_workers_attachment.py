"""T-CAT.W2 — workers/attachment.py: thin task wiring, no business logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog


@pytest.mark.asyncio
async def test_attachment_process_task_delegates_to_service():
    """Task calls service.process(attachment_id) when the feature is enabled."""
    container = MagicMock()
    container.chat_attachment_service = AsyncMock()

    from ragent.workers.attachment import attachment_process_task

    with patch("ragent.bootstrap.composition.get_container", return_value=container):
        await attachment_process_task("ATT001")

    container.chat_attachment_service.process.assert_awaited_once_with("ATT001")


@pytest.mark.asyncio
async def test_attachment_process_task_skips_when_feature_disabled():
    """Task no-ops with a log when chat_attachment_service is None (RAGENT_KEK_BASE64 unset)."""
    container = MagicMock()
    container.chat_attachment_service = None

    from ragent.workers.attachment import attachment_process_task

    with (
        structlog.testing.capture_logs() as logs,
        patch("ragent.bootstrap.composition.get_container", return_value=container),
    ):
        await attachment_process_task("ATT001")  # must not raise

    events = [e["event"] for e in logs]
    assert "attachment.process_skipped_feature_disabled" in events
