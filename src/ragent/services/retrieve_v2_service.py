"""RetrieveV2Service — anti-IDOR ownership gate for /retrieve/v2 and the /mcp/v1 `retrieve` tool.

Zero-trust contract: every document_id in a request must exist AND be owned
by the authenticated caller before any ES query is built. Missing ids are
rejected identically to foreign ids (403, never 404) so the endpoint cannot
be used as a document-existence oracle. Unauthenticated callers fail closed —
no anonymous fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from ragent.repositories.document_repository import DocumentRepository

logger = structlog.get_logger(__name__)


class DocumentForbidden(Exception):
    """At least one requested document_id is not owned by the caller."""

    error_code = "DOCUMENT_FORBIDDEN"
    http_status = 403


class RetrieveV2Service:
    def __init__(self, document_repo: DocumentRepository) -> None:
        self._documents = document_repo

    async def assert_owner(self, user_id: str | None, document_ids: list[str]) -> None:
        """Raise DocumentForbidden unless EVERY id exists and belongs to user_id."""
        if not user_id:
            logger.warning("retrieve_v2.auth_required", document_count=len(document_ids))
            raise DocumentForbidden("authentication required")

        rows = await self._documents.get_by_document_ids(document_ids)
        denied = [
            doc_id
            for doc_id in document_ids
            if doc_id not in rows or rows[doc_id].create_user != user_id
        ]
        if denied:
            logger.warning(
                "retrieve_v2.forbidden",
                user_id=user_id,
                requested_count=len(document_ids),
                denied_count=len(denied),
            )
            raise DocumentForbidden(f"{len(denied)} of {len(document_ids)} ids denied")
        logger.info(
            "retrieve_v2.ownership_verified",
            user_id=user_id,
            document_count=len(document_ids),
        )
