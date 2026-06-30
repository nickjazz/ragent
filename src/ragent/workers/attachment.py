"""T-CAT.W2 — Attachment processing worker task.

Thin task-wiring only (docs/00_domain_map.md §workers: no business logic
beyond task wiring) — the upload/process state machine lives entirely in
`ChatAttachmentService.process()`.
"""

from __future__ import annotations

import structlog

from ragent.bootstrap.broker import broker
from ragent.errors.codes import TaskErrorCode

logger = structlog.get_logger(__name__)


@broker.task("attachment.process")
async def attachment_process_task(attachment_id: str) -> None:
    from ragent.bootstrap.composition import get_container

    container = get_container()
    if container.chat_attachment_service is None:
        # RAGENT_KEK_BASE64 not set on this worker process — feature disabled.
        # Mark the row FAILED rather than acking silently, which would leave
        # it stuck UPLOADED forever with no client-visible signal.
        logger.warning("attachment.process_skipped_feature_disabled", attachment_id=attachment_id)
        await container.attachment_repository.update_status(
            attachment_id,
            "FAILED",
            error_code=TaskErrorCode.ATTACHMENT_FEATURE_DISABLED,
            error_reason=(
                "Attachment processing is disabled on this worker (RAGENT_KEK_BASE64 unset)."
            ),
        )
        return

    await container.chat_attachment_service.process(attachment_id)
