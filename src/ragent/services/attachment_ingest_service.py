"""AttachmentIngestService — chat attachments ride the standard ingest pipeline.

Replaces the retired ChatAttachmentService (encrypted AST artifacts + dedicated
worker task). An attachment upload is a normal upload-ingest: bytes staged to
MinIO, a `documents` row (source_app="chat_attachment"), chunks in ES — plus a
`session_documents` link binding the document to its chatagent session.

The attachment wire contract (`AttachmentInfo`) is preserved by mapping
documents rows back onto the old 4-value status vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from ragent.schemas.attachments import AttachmentMime
from ragent.schemas.ingest import IngestMime
from ragent.utility.id_gen import new_id

if TYPE_CHECKING:
    from ragent.repositories.document_repository import DocumentRow
    from ragent.repositories.session_document_repository import SessionDocumentRepository
    from ragent.services.ingest_service import IngestService

logger = structlog.get_logger(__name__)

# Spec default; composition.py reads ATTACHMENT_MAX_SIZE_BYTES env and passes
# the runtime value via constructor kwarg.
ATTACHMENT_MAX_SIZE_BYTES_DEFAULT = 50 * 1024 * 1024

# Constant source_app keeps attachment documents filterable (and excludable)
# in corpus-wide retrieval surfaces.
ATTACHMENT_SOURCE_APP = "chat_attachment"

# Explicit wire→ingest MIME mapping. Values are identical today, but the two
# enums evolve independently (ingest also accepts CSV); the lockstep unit test
# fails loudly if an AttachmentMime member ever lacks an ingest counterpart.
ATTACHMENT_TO_INGEST_MIME: dict[AttachmentMime, IngestMime] = {
    m: IngestMime(m.value) for m in AttachmentMime
}

# documents.status (5 values) → attachment wire status (4 values, unchanged
# contract). PENDING (worker claimed) and DELETING (transient pre-removal)
# both read as "still being worked on" to a polling client.
_STATUS_MAP = {
    "UPLOADED": "UPLOADED",
    "PENDING": "PROCESSING",
    "DELETING": "PROCESSING",
    "READY": "READY",
    "FAILED": "FAILED",
}


@dataclass
class AttachmentView:
    """AttachmentInfo-shaped projection of a documents row."""

    attachment_id: str
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    error_code: str | None = None
    error_reason: str | None = None


def _to_view(doc: DocumentRow) -> AttachmentView:
    return AttachmentView(
        attachment_id=doc.document_id,
        filename=doc.source_title,
        mime_type=doc.mime_type or "",
        size_bytes=doc.size_bytes or 0,
        status=_STATUS_MAP.get(doc.status, doc.status),
        error_code=doc.error_code,
        error_reason=doc.error_reason,
    )


class AttachmentIngestService:
    def __init__(
        self,
        ingest_service: IngestService,
        session_document_repo: SessionDocumentRepository,
        document_repo,
        max_size_bytes: int = ATTACHMENT_MAX_SIZE_BYTES_DEFAULT,
    ) -> None:
        self._ingest = ingest_service
        self._session_docs = session_document_repo
        self._documents = document_repo
        self._max_size_bytes = max_size_bytes

    async def upload(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        thread_id: str,
        create_user: str,
        mime_type: AttachmentMime,
    ) -> str:
        """Stage + ingest an attachment; returns the document_id, which IS the
        wire-level attachmentId. source_id is minted fresh per upload so the
        (source_id, source_app) supersede election never fires — re-uploading
        the same filename yields a new independent document."""
        logger.info(
            "attachment.upload_started",
            thread_id=thread_id,
            user_id=create_user,
            size_bytes=len(file_bytes),
        )
        document_id = await self._ingest.create_from_upload(
            create_user=create_user,
            source_id=new_id(),
            source_app=ATTACHMENT_SOURCE_APP,
            source_title=filename,
            mime_type=ATTACHMENT_TO_INGEST_MIME[mime_type],
            data=file_bytes,
            source_meta=thread_id,
            max_upload_bytes=self._max_size_bytes,
            persist_size_bytes=True,
        )
        await self._session_docs.create(
            session_id=thread_id, document_id=document_id, create_user=create_user
        )
        logger.info(
            "attachment.upload_linked",
            thread_id=thread_id,
            document_id=document_id,
            user_id=create_user,
        )
        return document_id

    async def get(self, document_id: str, create_user: str) -> AttachmentView | None:
        """Owner-scoped single lookup — the session_documents link is the
        authorization boundary; foreign/unknown ids never touch documents."""
        link = await self._session_docs.get_by_document(document_id, create_user=create_user)
        if link is None:
            return None
        doc = await self._documents.get(document_id)
        return _to_view(doc) if doc else None

    async def list_by_thread(self, thread_id: str, create_user: str) -> list[AttachmentView]:
        links = await self._session_docs.list_by_session(thread_id, create_user=create_user)
        return await self._views_for_links(links)

    async def list_by_user(self, create_user: str) -> list[AttachmentView]:
        links = await self._session_docs.list_by_user(create_user)
        return await self._views_for_links(links)

    async def _views_for_links(self, links) -> list[AttachmentView]:
        if not links:
            return []
        docs = await self._documents.get_by_document_ids([link.document_id for link in links])
        views = [_to_view(docs[link.document_id]) for link in links if link.document_id in docs]
        dropped = len(links) - len(views)
        if dropped:
            # A link whose document row is gone (deleted out-of-band) is
            # invisible, not an error — but never silently (00_rule §logs).
            logger.info(
                "attachment.list.dropped",
                dropped_count=dropped,
                before_count=len(links),
                after_count=len(views),
            )
        return views

    async def delete(self, document_id: str, create_user: str) -> bool:
        """Owner-scoped delete: ingest cascade (ES chunks + documents row)
        then the session link. Returns False when the caller does not own a
        link to the document (indistinguishable from absent)."""
        link = await self._session_docs.get_by_document(document_id, create_user=create_user)
        if link is None:
            return False
        await self._ingest.delete(document_id)
        await self._session_docs.delete_by_document(document_id, create_user=create_user)
        logger.info(
            "attachment.deleted",
            document_id=document_id,
            thread_id=link.session_id,
            user_id=create_user,
        )
        return True

    async def delete_by_session(self, session_id: str) -> None:
        """Cascade for DELETE /chatagent/v3/session — unlink every document in
        the session and delete each through the ingest cascade."""
        doc_ids = await self._session_docs.delete_by_session(session_id)
        results = await asyncio.gather(
            *[self._ingest.delete(doc_id) for doc_id in doc_ids],
            return_exceptions=True,
        )
        for doc_id, res in zip(doc_ids, results, strict=True):
            if isinstance(res, BaseException):
                logger.error(
                    "attachment.delete_failed",
                    document_id=doc_id,
                    error=str(res),
                )
        logger.info(
            "attachment.session_deleted",
            thread_id=session_id,
            document_count=len(doc_ids),
        )
