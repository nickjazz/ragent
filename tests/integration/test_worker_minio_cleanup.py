"""Worker MinIO retention policy."""

import datetime
from unittest.mock import patch

from ragent.repositories.document_repository import DocumentRow
from tests.conftest import make_ingest_container


def _make_doc(doc_status: str = "UPLOADED", ingest_type: str = "inline") -> DocumentRow:
    return DocumentRow(
        document_id="DOC001",
        create_user="alice",
        source_id="S1",
        source_app="confluence",
        source_title="T",
        source_meta=None,
        object_key="confluence_S1_DOC001",
        status=doc_status,
        attempt=1,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        updated_at=datetime.datetime.now(datetime.timezone.utc),
        ingest_type=ingest_type,
        minio_site="caller-site" if ingest_type == "file" else None,
    )


async def test_inline_type_ingest_does_not_delete_minio_object():
    """MinIO is retained for audit/replay even after a successful READY transition."""
    from ragent.workers.ingest import ingest_pipeline_task

    doc = _make_doc()
    container = make_ingest_container(doc)

    with patch("ragent.bootstrap.composition.get_container", return_value=container):
        await ingest_pipeline_task("DOC001")

    container.minio_registry.delete_object.assert_not_called()
    container.doc_repo.promote_to_ready_and_demote_siblings.assert_awaited_once()


async def test_pending_retry_does_not_delete_minio():
    """On pipeline failure, MinIO object must not be deleted."""
    from ragent.workers.ingest import ingest_pipeline_task

    doc = _make_doc()
    container = make_ingest_container(doc, pipeline_side_effect=RuntimeError("pipeline failed"))

    with patch("ragent.bootstrap.composition.get_container", return_value=container):
        await ingest_pipeline_task("DOC001")

    container.minio_registry.delete_object.assert_not_called()


async def test_self_demote_skips_post_ready_fan_out():
    """A self-demoted worker must not run post-READY enrichment."""
    from ragent.workers.ingest import ingest_pipeline_task

    doc = _make_doc()
    container = make_ingest_container(doc)
    container.doc_repo.promote_to_ready_and_demote_siblings.return_value = False

    with patch("ragent.bootstrap.composition.get_container", return_value=container):
        await ingest_pipeline_task("DOC001")

    container.registry.fan_out.assert_not_called()


async def test_file_type_ingest_does_not_delete_minio_object():
    """ingest_type='file': caller owns the object; worker must never delete it."""
    from ragent.workers.ingest import ingest_pipeline_task

    doc = _make_doc(ingest_type="file")
    container = make_ingest_container(doc)

    with patch("ragent.bootstrap.composition.get_container", return_value=container):
        await ingest_pipeline_task("DOC001")

    container.minio_registry.delete_object.assert_not_called()
    container.doc_repo.promote_to_ready_and_demote_siblings.assert_awaited_once()


async def test_upload_type_ingest_does_not_delete_minio_object():
    """ingest_type='upload': server-staged bytes are also retained."""
    from ragent.workers.ingest import ingest_pipeline_task

    doc = _make_doc(ingest_type="upload")
    container = make_ingest_container(doc)

    with patch("ragent.bootstrap.composition.get_container", return_value=container):
        await ingest_pipeline_task("DOC001")

    container.minio_registry.delete_object.assert_not_called()
    container.doc_repo.promote_to_ready_and_demote_siblings.assert_awaited_once()
