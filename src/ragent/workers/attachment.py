"""T-CAT.W2 — Attachment processing worker task.

Thin task-wiring only (docs/00_domain_map.md §workers: no business logic
beyond task wiring) — the upload/process state machine lives entirely in
`ChatAttachmentService.process()`.
"""

from __future__ import annotations

import structlog

from ragent.bootstrap.broker import broker

logger = structlog.get_logger(__name__)


@broker.task("attachment.process")
async def attachment_process_task(attachment_id: str) -> None:
    from ragent.bootstrap.composition import get_container

    container = get_container()
    if container.chat_attachment_service is None:
        # RAGENT_KEK_BASE64 not set on this worker process — feature disabled.
        logger.warning("attachment.process_skipped_feature_disabled", attachment_id=attachment_id)
        return

    await container.chat_attachment_service.process(attachment_id)
