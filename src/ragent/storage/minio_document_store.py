"""MinIODocumentStore — DocumentStore adapter over MinioSiteRegistry (T-CAT.6).

Bound to a single site at construction; the chat-attachment service depends
on the `DocumentStore` Protocol only (Dependency Inversion), never on MinIO.
"""

from __future__ import annotations

import io

from ragent.storage.minio_registry import DEFAULT_SITE, MinioSiteRegistry


class MinIODocumentStore:
    """DocumentStore adapter — delegates to the registry's caller-supplied-key
    methods (`get_object`/`delete_object`/`stat_object`) and the generic
    `put_object`."""

    def __init__(self, registry: MinioSiteRegistry, *, site: str = DEFAULT_SITE) -> None:
        self._registry = registry
        self._site = site

    def put(self, object_key: str, data: bytes, *, content_type: str) -> None:
        self._registry.put_object(self._site, object_key, io.BytesIO(data), len(data), content_type)

    def get(self, object_key: str) -> bytes:
        return self._registry.get_object(self._site, object_key)

    def delete(self, object_key: str) -> None:
        self._registry.delete_object(self._site, object_key)

    def exists(self, object_key: str) -> bool:
        return self._registry.stat_object(self._site, object_key) is not None
