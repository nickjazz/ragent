"""IngestService.delete cascade order and MinIO retention policy."""

import datetime
from unittest.mock import AsyncMock, MagicMock

from ragent.repositories.document_repository import DocumentRow
from ragent.services.ingest_service import IngestService


def _dt():
    return datetime.datetime.now(datetime.timezone.utc)


def _make_doc(**kwargs):
    base = dict(
        document_id="DOCID001",
        create_user="alice",
        source_id="S1",
        source_app="confluence",
        source_title="T",
        source_meta=None,
        object_key="confluence_S1_DOCID001",
        status="READY",
        attempt=1,
        created_at=_dt(),
        updated_at=_dt(),
    )
    base.update(kwargs)
    return DocumentRow(**base)


def _make_service(doc=None, not_claimable=False):
    repo = AsyncMock()
    doc = doc or _make_doc()
    if not_claimable:
        repo.claim_for_deletion.return_value = None
    else:
        repo.claim_for_deletion.return_value = doc

    storage = MagicMock()
    plugin_registry = MagicMock()
    plugin_registry.fan_out_delete = AsyncMock(return_value=[])

    svc = IngestService(
        repo=repo, storage=storage, broker=plugin_registry, registry=plugin_registry
    )
    return svc, repo, storage, plugin_registry


async def test_delete_ready_doc_calls_cascade_in_order():
    """claim_for_deletion atomically sets DELETING before external cleanup."""
    call_order = []
    doc = _make_doc()
    svc, repo, storage, registry = _make_service(doc=doc)
    repo.claim_for_deletion.side_effect = lambda doc_id: (
        call_order.append("claim_for_deletion") or doc
    )

    async def _fan_out(*_a):
        call_order.append("fan_out_delete")
        return []

    registry.fan_out_delete = AsyncMock(side_effect=_fan_out)
    repo.delete.side_effect = AsyncMock(side_effect=lambda *a: call_order.append("delete_row"))
    storage.delete_object.side_effect = lambda *a: call_order.append("delete_minio")

    await svc.delete("DOCID001")

    assert call_order == ["claim_for_deletion", "fan_out_delete", "delete_row"]
    storage.delete_object.assert_not_called()


async def test_delete_idempotent_on_missing_doc():
    """Re-DELETE of an already-deleted/in-progress document returns without error."""
    repo = AsyncMock()
    repo.claim_for_deletion.return_value = None
    registry = MagicMock()
    registry.fan_out_delete = AsyncMock(return_value=[])
    svc = IngestService(repo=repo, storage=MagicMock(), broker=registry, registry=registry)
    await svc.delete("NONEXISTENT")
    repo.delete.assert_not_called()


async def test_delete_never_deletes_minio_for_any_ingest_type_or_status():
    for status in ("UPLOADED", "PENDING", "READY", "FAILED"):
        for ingest_type in ("inline", "upload", "file"):
            doc = _make_doc(
                status=status,
                ingest_type=ingest_type,
                minio_site="tenant-eu-1" if ingest_type == "file" else None,
            )
            svc, repo, storage, _ = _make_service(doc=doc)

            await svc.delete("DOCID001")

            storage.delete_object.assert_not_called()
            repo.delete.assert_called_once_with("DOCID001")


async def test_delete_calls_fan_out_delete_outside_tx():
    """fan_out_delete is called with no DB tx open; structural verification only."""
    svc, repo, storage, registry = _make_service()
    registry.fan_out_delete = AsyncMock(return_value=[])
    await svc.delete("DOCID001")
    registry.fan_out_delete.assert_awaited_once_with("DOCID001")
    storage.delete_object.assert_not_called()
