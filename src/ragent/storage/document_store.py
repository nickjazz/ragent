"""DocumentStore — narrow Protocol for chat-attachment raw-byte storage (T-CAT.6).

Dependency Inversion: `chat_attachment_service` depends on this Protocol, not
directly on MinIO. `MinIODocumentStore` is the only implementation today.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DocumentStore(Protocol):
    def put(self, object_key: str, data: bytes, *, content_type: str) -> None: ...
    def get(self, object_key: str) -> bytes: ...
    def delete(self, object_key: str) -> None: ...
    def exists(self, object_key: str) -> bool: ...
