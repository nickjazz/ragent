"""T-ATTACH-R.3c — worker maintenance cycle: mark exceeded FAILED, resume DELETING, redispatch."""

from __future__ import annotations

import datetime
from typing import Any

import structlog

from ragent.bootstrap.metrics import record_pipeline_outcome
from ragent.errors.codes import TaskErrorCode

logger = structlog.get_logger(__name__)


async def run_maintenance_cycle(
    repo: Any,
    registry: Any,
    dispatcher: Any,
    pending_stale_seconds: int,
    uploaded_stale_seconds: int,
    deleting_stale_seconds: int,
    max_attempts: int,
) -> None:
    """One maintenance pass: retire exceeded rows, resume stale deletions, redispatch stale."""
    now = datetime.datetime.now(datetime.timezone.utc)

    for doc in await repo.list_pending_exceeded(attempt_gt=max_attempts):
        try:
            await repo.update_status(
                doc.document_id,
                from_status="PENDING",
                to_status="FAILED",
                error_code=TaskErrorCode.PIPELINE_MAX_ATTEMPTS_EXCEEDED,
                error_reason=f"maintenance swept stuck PENDING after attempt={doc.attempt}",
            )
            record_pipeline_outcome(
                source_app=doc.source_app, mime_type=doc.mime_type, outcome="failed"
            )
            if registry is not None:
                await registry.fan_out_delete(doc.document_id)
            logger.info("maintenance.mark_failed", document_id=doc.document_id, attempt=doc.attempt)
        except Exception:
            logger.exception("maintenance.mark_failed_error", document_id=doc.document_id)

    deleting_before = now - datetime.timedelta(seconds=deleting_stale_seconds)
    for doc in await repo.list_deleting_stale(updated_before=deleting_before):
        try:
            if registry is not None:
                await registry.fan_out_delete(doc.document_id)
            await repo.delete(doc.document_id)
            logger.info("maintenance.delete_resumed", document_id=doc.document_id)
        except Exception:
            logger.exception("maintenance.delete_resume_error", document_id=doc.document_id)

    pending_before = now - datetime.timedelta(seconds=pending_stale_seconds)
    for doc in await repo.list_pending_stale(
        updated_before=pending_before, attempt_le=max_attempts
    ):
        await dispatcher.enqueue("ingest.pipeline", document_id=doc.document_id)
        logger.info("maintenance.redispatch_pending", document_id=doc.document_id)

    uploaded_before = now - datetime.timedelta(seconds=uploaded_stale_seconds)
    for doc in await repo.list_uploaded_stale(updated_before=uploaded_before):
        await dispatcher.enqueue("ingest.pipeline", document_id=doc.document_id)
        logger.info("maintenance.redispatch_uploaded", document_id=doc.document_id)
