"""T-ATTACH-R.1a — startup sweep: re-enqueue stale PENDING/UPLOADED rows on worker boot."""

from __future__ import annotations

import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def run_startup_sweep(
    repo: Any,
    dispatcher: Any,
    pending_stale_seconds: int,
    uploaded_stale_seconds: int,
    max_attempts: int = 5,
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)

    pending_before = now - datetime.timedelta(seconds=pending_stale_seconds)
    for doc in await repo.list_pending_stale(
        updated_before=pending_before, attempt_le=max_attempts
    ):
        await dispatcher.enqueue("ingest.pipeline", document_id=doc.document_id)
        logger.info("startup_sweep.redispatch_pending", document_id=doc.document_id)

    uploaded_before = now - datetime.timedelta(seconds=uploaded_stale_seconds)
    for doc in await repo.list_uploaded_stale(updated_before=uploaded_before):
        await dispatcher.enqueue("ingest.pipeline", document_id=doc.document_id)
        logger.info("startup_sweep.redispatch_uploaded", document_id=doc.document_id)
