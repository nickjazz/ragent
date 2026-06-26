"""T-CAT.8 — AttachmentRepository: async CRUD for `chat_attachments` +
`chat_attachment_artifacts` (docs/spec/chat_attachments.md §9).

CRUD only, no business logic (R3) — status-transition rules and the
upload/encrypt/persist orchestration live in `chat_attachment_service.py`
(T-CAT.11), not here.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


@dataclass
class AttachmentRow:
    attachment_id: str
    thread_id: str
    create_user: str
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @classmethod
    def from_mapping(cls, m: Any) -> AttachmentRow:
        return cls(
            attachment_id=m["attachment_id"],
            thread_id=m["thread_id"],
            create_user=m["create_user"],
            filename=m["filename"],
            mime_type=m["mime_type"],
            size_bytes=m["size_bytes"],
            status=m["status"],
            created_at=m["created_at"],
            updated_at=m["updated_at"],
        )


@dataclass
class ArtifactRow:
    attachment_id: str
    ast_type: str
    storage_key: str
    created_at: datetime.datetime

    @classmethod
    def from_mapping(cls, m: Any) -> ArtifactRow:
        return cls(
            attachment_id=m["attachment_id"],
            ast_type=m["ast_type"],
            storage_key=m["storage_key"],
            created_at=m["created_at"],
        )


class AttachmentRepository:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_all(self, stmt: Any, params: dict | None = None) -> list[Any]:
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt, params or {})
            return result.mappings().all()

    async def _fetch_first(self, stmt: Any, params: dict | None = None) -> Any | None:
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt, params or {})
            return result.mappings().first()

    async def _execute(self, stmt: Any, params: dict | None = None) -> Any:
        async with self._engine.begin() as conn:
            return await conn.execute(stmt, params or {})

    # ------------------------------------------------------------------
    # chat_attachments — CRUD
    # ------------------------------------------------------------------

    async def create(
        self,
        attachment_id: str,
        thread_id: str,
        create_user: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
    ) -> str:
        await self._execute(
            text(
                """
                INSERT INTO chat_attachments
                    (attachment_id, thread_id, create_user, filename, mime_type,
                     size_bytes, status, created_at, updated_at)
                VALUES
                    (:attachment_id, :thread_id, :create_user, :filename, :mime_type,
                     :size_bytes, 'UPLOADED', NOW(6), NOW(6))
                """
            ),
            {
                "attachment_id": attachment_id,
                "thread_id": thread_id,
                "create_user": create_user,
                "filename": filename,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
            },
        )
        return attachment_id

    async def get(self, attachment_id: str) -> AttachmentRow | None:
        row = await self._fetch_first(
            text("SELECT * FROM chat_attachments WHERE attachment_id = :id"),
            {"id": attachment_id},
        )
        return AttachmentRow.from_mapping(row) if row else None

    async def list_by_thread(
        self, thread_id: str, after: str | None = None, limit: int = 50
    ) -> list[AttachmentRow]:
        cursor_clause = " AND attachment_id < :after" if after else ""
        params: dict = {"thread_id": thread_id, "limit": limit}
        if after:
            params["after"] = after
        rows = await self._fetch_all(
            text(
                f"SELECT * FROM chat_attachments WHERE thread_id = :thread_id{cursor_clause}"
                " ORDER BY created_at DESC, attachment_id DESC LIMIT :limit"
            ),
            params,
        )
        return [AttachmentRow.from_mapping(r) for r in rows]

    async def update_status(self, attachment_id: str, status: str) -> None:
        await self._execute(
            text(
                "UPDATE chat_attachments SET status = :status, updated_at = NOW(6)"
                " WHERE attachment_id = :id"
            ),
            {"status": status, "id": attachment_id},
        )

    # ------------------------------------------------------------------
    # chat_attachment_artifacts — CRUD
    # ------------------------------------------------------------------

    async def add_artifact(self, attachment_id: str, ast_type: str, storage_key: str) -> None:
        await self._execute(
            text(
                """
                INSERT INTO chat_attachment_artifacts
                    (attachment_id, ast_type, storage_key, created_at)
                VALUES
                    (:attachment_id, :ast_type, :storage_key, NOW(6))
                """
            ),
            {"attachment_id": attachment_id, "ast_type": ast_type, "storage_key": storage_key},
        )

    async def get_artifacts(self, attachment_id: str) -> list[ArtifactRow]:
        rows = await self._fetch_all(
            text("SELECT * FROM chat_attachment_artifacts WHERE attachment_id = :id"),
            {"id": attachment_id},
        )
        return [ArtifactRow.from_mapping(r) for r in rows]
