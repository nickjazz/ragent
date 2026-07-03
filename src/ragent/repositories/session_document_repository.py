"""SessionDocumentRepository: async CRUD for the `session_documents` link table
(015_session_documents.sql).

Binds a chatagent session (twp-ai thread_id — same value) to the documents
uploaded in it. CRUD only, no business logic (R3) — ownership decisions and
the ingest hand-off live in the attachment service layer.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


@dataclass
class SessionDocumentRow:
    session_id: str
    document_id: str
    create_date: datetime.datetime
    create_user: str

    @classmethod
    def from_mapping(cls, m: Any) -> SessionDocumentRow:
        return cls(
            session_id=m["session_id"],
            document_id=m["document_id"],
            create_date=m["create_date"],
            create_user=m["create_user"],
        )


class SessionDocumentRepository:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def create(self, session_id: str, document_id: str, create_user: str) -> None:
        """Insert a session→document link; INSERT IGNORE keeps retries
        idempotent against uq_session_document."""
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT IGNORE INTO session_documents"
                    " (session_id, document_id, create_date, create_user)"
                    " VALUES (:session_id, :document_id, NOW(6), :create_user)"
                ),
                {
                    "session_id": session_id,
                    "document_id": document_id,
                    "create_user": create_user,
                },
            )

    async def list_by_session(self, session_id: str, create_user: str) -> list[SessionDocumentRow]:
        """Return session links newest-first (DESC) for session-fallback ordering."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT * FROM session_documents"
                    " WHERE session_id = :session_id AND create_user = :create_user"
                    " ORDER BY create_date DESC, id DESC"
                ),
                {"session_id": session_id, "create_user": create_user},
            )
            return [SessionDocumentRow.from_mapping(r) for r in result.mappings().all()]

    async def get_by_documents(
        self, document_ids: list[str], create_user: str
    ) -> list[SessionDocumentRow]:
        """Batch-fetch links for multiple document_ids owned by create_user."""
        if not document_ids:
            return []
        from sqlalchemy import bindparam

        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT * FROM session_documents"
                    " WHERE document_id IN :ids AND create_user = :create_user"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": document_ids, "create_user": create_user},
            )
            return [SessionDocumentRow.from_mapping(r) for r in result.mappings().all()]

    async def get_by_document(
        self, document_id: str, create_user: str
    ) -> SessionDocumentRow | None:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT * FROM session_documents"
                    " WHERE document_id = :document_id AND create_user = :create_user"
                ),
                {"document_id": document_id, "create_user": create_user},
            )
            row = result.mappings().first()
            return SessionDocumentRow.from_mapping(row) if row else None

    async def list_by_user(self, create_user: str) -> list[SessionDocumentRow]:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT * FROM session_documents WHERE create_user = :create_user"
                    " ORDER BY create_date DESC, id DESC"
                ),
                {"create_user": create_user},
            )
            return [SessionDocumentRow.from_mapping(r) for r in result.mappings().all()]

    async def delete_by_document(self, document_id: str, create_user: str) -> bool:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    "DELETE FROM session_documents"
                    " WHERE document_id = :document_id AND create_user = :create_user"
                ),
                {"document_id": document_id, "create_user": create_user},
            )
            return result.rowcount > 0

    async def delete_by_session(self, session_id: str) -> list[str]:
        """Delete every link in a session; returns the unlinked document_ids
        so the caller can cascade the underlying document deletes."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("SELECT document_id FROM session_documents WHERE session_id = :session_id"),
                {"session_id": session_id},
            )
            doc_ids = [r["document_id"] for r in result.mappings().all()]
            await conn.execute(
                text("DELETE FROM session_documents WHERE session_id = :session_id"),
                {"session_id": session_id},
            )
            return doc_ids
