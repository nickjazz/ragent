"""AttachmentContextResolver — metadata-only <attachments> context for /chatagent/v3.

Replaces the retired DocumentArtifactResolver (which decrypted and inlined
file content). Under the MCP-driven design the LLM never receives file bodies
in context: the block lists documentId/filename metadata and an instruction
directs the model to call the (document-scoped) retrieve tool autonomously.

Two injection modes:
- explicit `attachment_ids` — the user attached files to THIS message; the
  instruction mandates retrieving ALL of them before answering.
- session fallback (no ids) — every file previously uploaded in the session
  is listed newest-first, the newest flagged `"latest": true`, and the
  instruction prioritises that document. A session with no files yields None:
  no block, no instruction, zero prompt overhead for ordinary conversations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from ragent.repositories.document_repository import DocumentRepository
    from ragent.repositories.session_document_repository import (
        SessionDocumentRepository,
        SessionDocumentRow,
    )

logger = structlog.get_logger(__name__)

EXPLICIT_INSTRUCTION = (
    "[Instruction] The user attached the files listed above to this message. "
    "Do not guess their content. Before answering any question about them, you "
    "MUST call the retrieve tool with document_id_list containing ALL the "
    "documentId values listed above."
)

SESSION_INSTRUCTION = (
    "[Instruction] The files listed above were previously uploaded in this "
    "conversation. Do not guess their content. For any question about these "
    'files, FIRST call the retrieve tool with the documentId marked "latest": '
    "true. If the retrieved chunks are insufficient to answer, retrieve the "
    "remaining documentId values as needed."
)


@dataclass
class AttachmentContext:
    """Resolved attachment context: JSON array for the <attachments> block plus
    the instruction line appended after the </hidden> wrapper."""

    files_json: str
    instruction: str


class AttachmentContextResolver:
    def __init__(
        self,
        session_document_repo: SessionDocumentRepository,
        document_repo: DocumentRepository,
    ) -> None:
        self._session_docs = session_document_repo
        self._documents = document_repo

    async def resolve(
        self,
        *,
        session_id: str,
        user_id: str,
        attachment_ids: list[str] | None = None,
    ) -> AttachmentContext | None:
        if attachment_ids:
            return await self._resolve_explicit(user_id, attachment_ids)
        return await self._resolve_session(session_id, user_id)

    async def _resolve_explicit(
        self, user_id: str, attachment_ids: list[str]
    ) -> AttachmentContext | None:
        # Batch-fetch all links in one query; unknown/foreign ids produce no row
        # (ownership encoded in the WHERE clause — no existence oracle leak).
        found = await self._session_docs.get_by_documents(attachment_ids, create_user=user_id)
        found_ids = {link.document_id for link in found}
        for att_id in attachment_ids:
            if att_id not in found_ids:
                logger.warning(
                    "attachment_context.attachment_not_found",
                    attachment_id=att_id,
                    user_id=user_id,
                )
        files = await self._files_for_links(found)
        if not files:
            return None
        logger.info(
            "attachment_context.resolved",
            mode="explicit",
            user_id=user_id,
            file_count=len(files),
        )
        return AttachmentContext(
            files_json=json.dumps(files, ensure_ascii=False),
            instruction=EXPLICIT_INSTRUCTION,
        )

    async def _resolve_session(self, session_id: str, user_id: str) -> AttachmentContext | None:
        # list_by_session returns newest-first (DESC); no re-sort needed.
        links = await self._session_docs.list_by_session(session_id, create_user=user_id)
        if not links:
            return None
        files = await self._files_for_links(links)
        if not files:
            return None
        for i, entry in enumerate(files):
            entry["latest"] = i == 0
        logger.info(
            "attachment_context.resolved",
            mode="session_fallback",
            thread_id=session_id,
            user_id=user_id,
            file_count=len(files),
        )
        return AttachmentContext(
            files_json=json.dumps(files, ensure_ascii=False),
            instruction=SESSION_INSTRUCTION,
        )

    async def _files_for_links(self, links: list[SessionDocumentRow]) -> list[dict[str, Any]]:
        if not links:
            return []
        docs = await self._documents.get_by_document_ids([link.document_id for link in links])
        files = []
        for link in links:
            doc = docs.get(link.document_id)
            if doc is None:
                logger.warning("attachment_context.document_missing", document_id=link.document_id)
                continue
            files.append(
                {
                    "documentId": link.document_id,
                    "filename": doc.source_title,
                    "uploadedAt": link.create_date.isoformat(),
                }
            )
        return files
