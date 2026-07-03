"""AttachmentIngestService: ingest-backed chat attachments (unit, mocked collaborators)."""

import datetime
from unittest.mock import AsyncMock

import pytest

from ragent.repositories.document_repository import DocumentRepository, DocumentRow
from ragent.repositories.session_document_repository import (
    SessionDocumentRepository,
    SessionDocumentRow,
)
from ragent.schemas.attachments import AttachmentMime
from ragent.schemas.ingest import IngestMime
from ragent.services.attachment_ingest_service import (
    ATTACHMENT_SOURCE_APP,
    ATTACHMENT_TO_INGEST_MIME,
    AttachmentIngestService,
)
from ragent.services.ingest_service import IngestService

_DOC_ID = "DOCAAAAAAAAAAAAAAAAAAAAAA"


def _dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)


def _doc_row(**kwargs) -> DocumentRow:
    base = dict(
        document_id=_DOC_ID,
        create_user="alice",
        source_id="SRC1",
        source_app=ATTACHMENT_SOURCE_APP,
        source_title="report.pdf",
        source_meta="thread-1",
        object_key="k",
        status="READY",
        attempt=0,
        created_at=_dt("2026-01-01T00:00:00"),
        updated_at=_dt("2026-01-01T00:00:00"),
        mime_type="application/pdf",
        size_bytes=123,
    )
    base.update(kwargs)
    return DocumentRow(**base)


def _link_row(**kwargs) -> SessionDocumentRow:
    base = dict(
        session_id="thread-1",
        document_id=_DOC_ID,
        create_date=_dt("2026-01-01T00:00:00"),
        create_user="alice",
    )
    base.update(kwargs)
    return SessionDocumentRow(**base)


def _service(**overrides):
    ingest = AsyncMock(spec=IngestService)
    ingest.create_from_upload.return_value = _DOC_ID
    session_docs = AsyncMock(spec=SessionDocumentRepository)
    doc_repo = AsyncMock(spec=DocumentRepository)
    kwargs = dict(
        ingest_service=ingest,
        session_document_repo=session_docs,
        document_repo=doc_repo,
        max_size_bytes=1024,
    )
    kwargs.update(overrides)
    return AttachmentIngestService(**kwargs), ingest, session_docs, doc_repo


# ---------------------------------------------------------------------------
# MIME mapping lockstep
# ---------------------------------------------------------------------------


def test_attachment_mime_mapping_is_total():
    """Every wire-level AttachmentMime maps to a valid IngestMime — drift in
    either enum breaks this loudly instead of at upload time."""
    for mime in AttachmentMime:
        assert mime in ATTACHMENT_TO_INGEST_MIME
        assert isinstance(ATTACHMENT_TO_INGEST_MIME[mime], IngestMime)


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_delegates_to_ingest_and_links_session():
    svc, ingest, session_docs, _ = _service()

    doc_id = await svc.upload(
        file_bytes=b"hello",
        filename="notes.md",
        thread_id="thread-1",
        create_user="alice",
        mime_type=AttachmentMime.TEXT_MARKDOWN,
    )

    assert doc_id == _DOC_ID
    kwargs = ingest.create_from_upload.call_args.kwargs
    assert kwargs["create_user"] == "alice"
    assert kwargs["source_app"] == ATTACHMENT_SOURCE_APP
    assert kwargs["source_title"] == "notes.md"
    assert kwargs["source_meta"] == "thread-1"
    assert kwargs["mime_type"] == IngestMime.TEXT_MARKDOWN
    assert kwargs["data"] == b"hello"
    assert kwargs["max_upload_bytes"] == 1024
    assert kwargs["persist_size_bytes"] is True
    session_docs.create.assert_awaited_once_with(
        session_id="thread-1", document_id=_DOC_ID, create_user="alice"
    )


@pytest.mark.asyncio
async def test_upload_mints_unique_source_id_per_upload():
    """Unique source_id per upload disables supersede — same filename twice
    in the same thread must produce two live documents."""
    svc, ingest, _, _ = _service()

    await svc.upload(
        file_bytes=b"a",
        filename="same.md",
        thread_id="thread-1",
        create_user="alice",
        mime_type=AttachmentMime.TEXT_MARKDOWN,
    )
    await svc.upload(
        file_bytes=b"b",
        filename="same.md",
        thread_id="thread-1",
        create_user="alice",
        mime_type=AttachmentMime.TEXT_MARKDOWN,
    )

    first = ingest.create_from_upload.call_args_list[0].kwargs["source_id"]
    second = ingest.create_from_upload.call_args_list[1].kwargs["source_id"]
    assert first != second


# ---------------------------------------------------------------------------
# reads — status mapping onto the 4-value attachment contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc_status", "expected"),
    [
        ("UPLOADED", "UPLOADED"),
        ("PENDING", "PROCESSING"),
        ("DELETING", "PROCESSING"),
        ("READY", "READY"),
        ("FAILED", "FAILED"),
    ],
)
@pytest.mark.asyncio
async def test_get_maps_document_status_to_attachment_vocabulary(doc_status, expected):
    svc, _, session_docs, doc_repo = _service()
    session_docs.get_by_document.return_value = _link_row()
    doc_repo.get.return_value = _doc_row(status=doc_status)

    view = await svc.get(_DOC_ID, create_user="alice")

    assert view is not None
    assert view.status == expected
    assert view.attachment_id == _DOC_ID
    assert view.filename == "report.pdf"
    assert view.mime_type == "application/pdf"
    assert view.size_bytes == 123


@pytest.mark.asyncio
async def test_get_returns_none_when_link_missing():
    """Ownership/session scoping happens on the link table — a foreign or
    unknown id never reaches the documents table."""
    svc, _, session_docs, doc_repo = _service()
    session_docs.get_by_document.return_value = None

    assert await svc.get(_DOC_ID, create_user="mallory") is None
    doc_repo.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_surfaces_error_diagnostics():
    svc, _, session_docs, doc_repo = _service()
    session_docs.get_by_document.return_value = _link_row()
    doc_repo.get.return_value = _doc_row(
        status="FAILED", error_code="EMBEDDER_ERROR", error_reason="boom"
    )

    view = await svc.get(_DOC_ID, create_user="alice")

    assert view.status == "FAILED"
    assert view.error_code == "EMBEDDER_ERROR"
    assert view.error_reason == "boom"


@pytest.mark.asyncio
async def test_list_by_thread_joins_links_to_documents():
    other_id = "DOCBBBBBBBBBBBBBBBBBBBBBB"
    svc, _, session_docs, doc_repo = _service()
    session_docs.list_by_session.return_value = [
        _link_row(),
        _link_row(document_id=other_id),
    ]
    doc_repo.get_by_document_ids.return_value = {
        _DOC_ID: _doc_row(),
        other_id: _doc_row(document_id=other_id, status="PENDING", source_title="b.md"),
    }

    views = await svc.list_by_thread("thread-1", create_user="alice")

    assert [v.attachment_id for v in views] == [_DOC_ID, other_id]
    assert views[1].status == "PROCESSING"
    session_docs.list_by_session.assert_awaited_once_with("thread-1", create_user="alice")


@pytest.mark.asyncio
async def test_list_by_thread_skips_links_with_missing_documents():
    svc, _, session_docs, doc_repo = _service()
    session_docs.list_by_session.return_value = [_link_row()]
    doc_repo.get_by_document_ids.return_value = {}

    assert await svc.list_by_thread("thread-1", create_user="alice") == []


@pytest.mark.asyncio
async def test_list_by_user_joins_links_to_documents():
    svc, _, session_docs, doc_repo = _service()
    session_docs.list_by_user.return_value = [_link_row()]
    doc_repo.get_by_document_ids.return_value = {_DOC_ID: _doc_row()}

    views = await svc.list_by_user("alice")

    assert len(views) == 1
    session_docs.list_by_user.assert_awaited_once_with("alice")


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_cascades_ingest_delete_and_unlinks():
    svc, ingest, session_docs, _ = _service()
    session_docs.get_by_document.return_value = _link_row()
    session_docs.delete_by_document.return_value = True

    assert await svc.delete(_DOC_ID, create_user="alice") is True
    ingest.delete.assert_awaited_once_with(_DOC_ID)
    session_docs.delete_by_document.assert_awaited_once_with(_DOC_ID, create_user="alice")


@pytest.mark.asyncio
async def test_delete_returns_false_for_foreign_or_unknown_id():
    svc, ingest, session_docs, _ = _service()
    session_docs.get_by_document.return_value = None

    assert await svc.delete(_DOC_ID, create_user="mallory") is False
    ingest.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_by_session_cascades_every_linked_document():
    other_id = "DOCBBBBBBBBBBBBBBBBBBBBBB"
    svc, ingest, session_docs, _ = _service()
    session_docs.delete_by_session.return_value = [_DOC_ID, other_id]

    await svc.delete_by_session("thread-1")

    assert ingest.delete.await_count == 2
    deleted = [c.args[0] for c in ingest.delete.await_args_list]
    assert deleted == [_DOC_ID, other_id]


@pytest.mark.asyncio
async def test_delete_by_session_continues_after_one_failure():
    """Fail-soft: a single delete error must not abort the remaining deletes."""
    other_id = "DOCBBBBBBBBBBBBBBBBBBBBBB"
    svc, ingest, session_docs, _ = _service()
    session_docs.delete_by_session.return_value = [_DOC_ID, other_id]
    ingest.delete.side_effect = [RuntimeError("es down"), None]

    await svc.delete_by_session("thread-1")  # must not raise

    assert ingest.delete.await_count == 2
