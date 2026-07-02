"""AttachmentContextResolver — metadata-only <attachments> block for /chatagent/v3.

Never injects file content: the block carries documentId/filename metadata and
an instruction directing the LLM to call the retrieve tool autonomously.
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ragent.repositories.document_repository import DocumentRepository
from ragent.repositories.session_document_repository import (
    SessionDocumentRepository,
    SessionDocumentRow,
)
from ragent.services.attachment_context_resolver import (
    AttachmentContext,
    AttachmentContextResolver,
)

_DOC_A = "DOCAAAAAAAAAAAAAAAAAAAAAA"
_DOC_B = "DOCBBBBBBBBBBBBBBBBBBBBBB"


def _dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)


def _link(document_id=_DOC_A, create_date="2026-01-01T00:00:00", **kwargs) -> SessionDocumentRow:
    base = dict(
        session_id="thread-1",
        document_id=document_id,
        create_date=_dt(create_date),
        create_user="alice",
    )
    base.update(kwargs)
    return SessionDocumentRow(**base)


def _doc(document_id=_DOC_A, filename="report.pdf"):
    return SimpleNamespace(document_id=document_id, source_title=filename)


def _resolver(links_by_doc=None, session_links=None, docs=None):
    session_repo = AsyncMock(spec=SessionDocumentRepository)
    _lbd = links_by_doc or {}
    session_repo.get_by_documents.side_effect = lambda doc_ids, create_user: [
        _lbd[did] for did in doc_ids if did in _lbd
    ]
    session_repo.list_by_session.return_value = session_links or []
    doc_repo = AsyncMock(spec=DocumentRepository)
    doc_repo.get_by_document_ids.return_value = docs or {}
    return AttachmentContextResolver(session_repo, doc_repo), session_repo, doc_repo


# ---------------------------------------------------------------------------
# Explicit attachment_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_ids_emit_metadata_only_block():
    resolver, _, _ = _resolver(
        links_by_doc={_DOC_A: _link()},
        docs={_DOC_A: _doc()},
    )

    ctx = await resolver.resolve(session_id="thread-1", user_id="alice", attachment_ids=[_DOC_A])

    assert isinstance(ctx, AttachmentContext)
    files = json.loads(ctx.files_json)
    assert files == [
        {
            "documentId": _DOC_A,
            "filename": "report.pdf",
            "uploadedAt": "2026-01-01T00:00:00+00:00",
        }
    ]
    assert "content" not in files[0]
    assert ctx.instruction.startswith("[Instruction]")
    assert "ALL the documentId values" in ctx.instruction


@pytest.mark.asyncio
async def test_explicit_ids_skip_foreign_or_unknown_ids():
    """Foreign/unknown ids are skipped with a warning — never injected."""
    resolver, _, _ = _resolver(
        links_by_doc={_DOC_A: _link()},
        docs={_DOC_A: _doc()},
    )

    ctx = await resolver.resolve(
        session_id="thread-1", user_id="alice", attachment_ids=[_DOC_A, "DOC_FOREIGN"]
    )

    files = json.loads(ctx.files_json)
    assert [f["documentId"] for f in files] == [_DOC_A]


@pytest.mark.asyncio
async def test_explicit_ids_all_foreign_returns_none():
    resolver, _, _ = _resolver(links_by_doc={}, docs={})

    ctx = await resolver.resolve(session_id="thread-1", user_id="mallory", attachment_ids=[_DOC_A])

    assert ctx is None


# ---------------------------------------------------------------------------
# Session fallback (no attachment_ids)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_fallback_lists_all_files_latest_first_with_latest_flag():
    # list_by_session returns newest-first (DESC); mock matches real SQL order.
    resolver, session_repo, _ = _resolver(
        session_links=[
            _link(document_id=_DOC_B, create_date="2026-02-01T00:00:00"),
            _link(document_id=_DOC_A, create_date="2026-01-01T00:00:00"),
        ],
        docs={_DOC_A: _doc(_DOC_A, "old.md"), _DOC_B: _doc(_DOC_B, "new.md")},
    )

    ctx = await resolver.resolve(session_id="thread-1", user_id="alice", attachment_ids=None)

    files = json.loads(ctx.files_json)
    # Newest first, and only the newest carries latest=true.
    assert [f["documentId"] for f in files] == [_DOC_B, _DOC_A]
    assert files[0]["latest"] is True
    assert files[1]["latest"] is False
    assert files[0]["filename"] == "new.md"
    assert '"latest": true' in ctx.instruction
    session_repo.list_by_session.assert_awaited_once_with("thread-1", create_user="alice")


@pytest.mark.asyncio
async def test_session_fallback_without_files_returns_none():
    """A conversation with no uploaded files gets NO block and NO instruction."""
    resolver, _, doc_repo = _resolver(session_links=[])

    assert (
        await resolver.resolve(session_id="thread-1", user_id="alice", attachment_ids=None) is None
    )
    doc_repo.get_by_document_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_attachment_ids_list_falls_back_to_session():
    resolver, session_repo, _ = _resolver(
        session_links=[_link()],
        docs={_DOC_A: _doc()},
    )

    ctx = await resolver.resolve(session_id="thread-1", user_id="alice", attachment_ids=[])

    assert ctx is not None
    session_repo.list_by_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_fallback_limit_caps_returned_files():
    """limit parameter caps the number of files from session fallback (newest-first)."""
    resolver, _, _ = _resolver(
        session_links=[
            _link(document_id=_DOC_B, create_date="2026-02-01T00:00:00"),
            _link(document_id=_DOC_A, create_date="2026-01-01T00:00:00"),
        ],
        docs={_DOC_A: _doc(_DOC_A, "old.md"), _DOC_B: _doc(_DOC_B, "new.md")},
    )

    ctx = await resolver.resolve(
        session_id="thread-1", user_id="alice", attachment_ids=None, limit=1
    )

    files = json.loads(ctx.files_json)
    assert [f["documentId"] for f in files] == [_DOC_B]
    assert files[0]["latest"] is True


@pytest.mark.asyncio
async def test_instructions_differ_between_explicit_and_fallback():
    resolver_explicit, _, _ = _resolver(links_by_doc={_DOC_A: _link()}, docs={_DOC_A: _doc()})
    resolver_fallback, _, _ = _resolver(session_links=[_link()], docs={_DOC_A: _doc()})

    explicit = await resolver_explicit.resolve(
        session_id="thread-1", user_id="alice", attachment_ids=[_DOC_A]
    )
    fallback = await resolver_fallback.resolve(
        session_id="thread-1", user_id="alice", attachment_ids=None
    )

    assert explicit.instruction != fallback.instruction
    # Both forbid guessing and mandate the retrieve tool.
    for instruction in (explicit.instruction, fallback.instruction):
        assert "Do not guess" in instruction
        assert "retrieve tool" in instruction
