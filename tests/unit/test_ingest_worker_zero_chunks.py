"""T-ATTACH-R.3b — zero-chunks integrity gate: pipeline writing 0 chunks → FAILED."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragent.repositories.document_repository import DocumentRow


def _doc() -> MagicMock:
    doc = MagicMock(spec=DocumentRow)
    doc.document_id = "DOC-ZERO-1"
    doc.minio_site = "__default__"
    doc.object_key = "file.txt"
    doc.source_id = "S1"
    doc.source_app = "test-app"
    doc.source_url = None
    doc.source_title = "File"
    doc.source_meta = None
    doc.ingest_type = "inline"
    doc.attempt = 0
    doc.mime_type = "text/plain"
    return doc


def _container(doc: MagicMock, *, documents_written: int) -> MagicMock:
    container = MagicMock()
    container.doc_repo = AsyncMock()
    container.doc_repo.claim_for_processing.return_value = doc
    container.doc_repo.update_status = AsyncMock()
    container.doc_repo.promote_to_ready_and_demote_siblings = AsyncMock(return_value=True)
    container.minio_registry = MagicMock()
    container.minio_registry.head_object.return_value = (10, "text/plain")
    container.minio_registry.get_object.return_value = b"hello"
    container.ingest_pipeline = MagicMock()
    container.ingest_pipeline.run.return_value = {
        "writer": {"documents_written": documents_written}
    }
    container.registry = MagicMock()
    container.registry.fan_out = AsyncMock()
    container.unprotect_client = None
    container.embedding_registry = MagicMock()
    container.embedding_registry.refresh = AsyncMock()
    container.heartbeat_tick = MagicMock()
    container.heartbeat_interval = 60.0
    container.max_attempts = 5
    container.pending_stale_seconds = 30
    return container


@pytest.mark.asyncio
async def test_zero_chunks_marks_failed_not_ready():
    """Pipeline returning 0 chunks must mark the row FAILED, not promote to READY."""
    doc = _doc()
    container = _container(doc, documents_written=0)

    from ragent.workers import ingest as worker_mod

    with patch("ragent.bootstrap.composition.get_container", return_value=container):
        await worker_mod.ingest_pipeline_task("DOC-ZERO-1")

    container.doc_repo.update_status.assert_awaited_once()
    call_kwargs = container.doc_repo.update_status.call_args.kwargs
    assert call_kwargs["to_status"] == "FAILED"
    container.doc_repo.promote_to_ready_and_demote_siblings.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonzero_chunks_proceeds_to_promote():
    """Pipeline returning >0 chunks proceeds to promote_to_ready_and_demote_siblings."""
    doc = _doc()
    container = _container(doc, documents_written=3)

    from ragent.workers import ingest as worker_mod

    with patch("ragent.bootstrap.composition.get_container", return_value=container):
        await worker_mod.ingest_pipeline_task("DOC-ZERO-1")

    container.doc_repo.promote_to_ready_and_demote_siblings.assert_awaited_once()
