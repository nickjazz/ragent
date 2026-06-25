"""Tests for MinIODocumentStore (T-CAT.6) — DocumentStore adapter over MinioSiteRegistry."""

from unittest.mock import MagicMock, create_autospec

import pytest

from ragent.storage.document_store import DocumentStore
from ragent.storage.minio_document_store import MinIODocumentStore
from ragent.storage.minio_registry import MinioSiteRegistry


@pytest.fixture
def registry() -> MagicMock:
    return create_autospec(MinioSiteRegistry, instance=True)


def test_minio_document_store_satisfies_document_store_protocol(registry: MagicMock):
    store = MinIODocumentStore(registry)
    assert isinstance(store, DocumentStore)


def test_put_delegates_to_registry_put_object(registry: MagicMock):
    store = MinIODocumentStore(registry, site="tenant-eu-1")

    store.put("att_123/complete", b"payload", content_type="application/json")

    registry.put_object.assert_called_once()
    args = registry.put_object.call_args.args
    assert args[0] == "tenant-eu-1"
    assert args[1] == "att_123/complete"
    assert args[2].read() == b"payload"
    assert args[3] == len(b"payload")
    assert args[4] == "application/json"


def test_put_uses_default_site_when_unspecified(registry: MagicMock):
    store = MinIODocumentStore(registry)

    store.put("key", b"x", content_type="text/plain")

    assert registry.put_object.call_args.args[0] == "__default__"


def test_get_delegates_to_registry_get_object(registry: MagicMock):
    registry.get_object.return_value = b"decrypted-bytes"
    store = MinIODocumentStore(registry)

    result = store.get("att_123/complete")

    assert result == b"decrypted-bytes"
    registry.get_object.assert_called_once_with("__default__", "att_123/complete")


def test_delete_delegates_to_registry_delete_object(registry: MagicMock):
    store = MinIODocumentStore(registry)

    store.delete("att_123/complete")

    registry.delete_object.assert_called_once_with("__default__", "att_123/complete")


def test_exists_true_when_stat_object_returns_size(registry: MagicMock):
    registry.stat_object.return_value = 42
    store = MinIODocumentStore(registry)

    assert store.exists("att_123/complete") is True


def test_exists_false_when_stat_object_returns_none(registry: MagicMock):
    registry.stat_object.return_value = None
    store = MinIODocumentStore(registry)

    assert store.exists("att_123/complete") is False
