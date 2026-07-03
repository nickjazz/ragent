"""SessionDocumentRepository: CRUD for the `session_documents` link table (unit, mocked conn)."""

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragent.repositories.session_document_repository import (
    SessionDocumentRepository,
    SessionDocumentRow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)


def _row(**kwargs) -> dict:
    base = dict(
        session_id="thread-1",
        document_id="DOCAAAAAAAAAAAAAAAAAAAAAA",
        create_date=_dt("2026-01-01T00:00:00"),
        create_user="alice",
    )
    base.update(kwargs)
    return base


def _mock_engine(rows=None, rowcount=1):
    """AsyncMock engine for `async with engine.begin() as conn` repositories."""
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows or []
    result.mappings.return_value.first.return_value = rows[0] if rows else None
    result.rowcount = rowcount

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=result)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.begin = MagicMock(return_value=ctx)
    return engine, conn


def _executed_sql(conn) -> str:
    return " ".join(str(call.args[0]) for call in conn.execute.call_args_list)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_inserts_link_row():
    engine, conn = _mock_engine()
    repo = SessionDocumentRepository(engine)

    await repo.create(
        session_id="thread-1",
        document_id="DOCAAAAAAAAAAAAAAAAAAAAAA",
        create_user="alice",
    )

    sql = _executed_sql(conn)
    assert "INSERT" in sql
    assert "session_documents" in sql
    params = conn.execute.call_args.args[1]
    assert params["session_id"] == "thread-1"
    assert params["document_id"] == "DOCAAAAAAAAAAAAAAAAAAAAAA"
    assert params["create_user"] == "alice"


@pytest.mark.asyncio
async def test_create_is_idempotent_on_duplicate_link():
    """uq_session_document guards duplicates; INSERT IGNORE keeps retries safe."""
    engine, conn = _mock_engine(rowcount=0)
    repo = SessionDocumentRepository(engine)

    await repo.create(
        session_id="thread-1",
        document_id="DOCAAAAAAAAAAAAAAAAAAAAAA",
        create_user="alice",
    )

    assert "INSERT IGNORE" in _executed_sql(conn)


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_session_orders_by_create_date_and_filters_user():
    rows = [_row(), _row(document_id="DOCBBBBBBBBBBBBBBBBBBBBBB")]
    engine, conn = _mock_engine(rows=rows)
    repo = SessionDocumentRepository(engine)

    out = await repo.list_by_session("thread-1", create_user="alice")

    assert [r.document_id for r in out] == [
        "DOCAAAAAAAAAAAAAAAAAAAAAA",
        "DOCBBBBBBBBBBBBBBBBBBBBBB",
    ]
    assert all(isinstance(r, SessionDocumentRow) for r in out)
    sql = _executed_sql(conn)
    assert "session_id = :session_id" in sql
    assert "create_user = :create_user" in sql
    assert "ORDER BY create_date DESC" in sql


@pytest.mark.asyncio
async def test_get_by_documents_batch_scopes_by_user():
    rows = [_row(), _row(document_id="DOCBBBBBBBBBBBBBBBBBBBBBB")]
    engine, conn = _mock_engine(rows=rows)
    repo = SessionDocumentRepository(engine)

    out = await repo.get_by_documents(
        ["DOCAAAAAAAAAAAAAAAAAAAAAA", "DOCBBBBBBBBBBBBBBBBBBBBBB"], create_user="alice"
    )

    assert len(out) == 2
    sql = _executed_sql(conn)
    assert "document_id IN" in sql
    assert "create_user = :create_user" in sql


@pytest.mark.asyncio
async def test_get_by_documents_empty_list_returns_no_rows():
    engine, conn = _mock_engine(rows=[])
    repo = SessionDocumentRepository(engine)

    out = await repo.get_by_documents([], create_user="alice")

    assert out == []
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_by_document_scopes_by_user():
    engine, conn = _mock_engine(rows=[_row()])
    repo = SessionDocumentRepository(engine)

    row = await repo.get_by_document("DOCAAAAAAAAAAAAAAAAAAAAAA", create_user="alice")

    assert row is not None
    assert row.session_id == "thread-1"
    sql = _executed_sql(conn)
    assert "document_id = :document_id" in sql
    assert "create_user = :create_user" in sql


@pytest.mark.asyncio
async def test_get_by_document_returns_none_when_absent():
    engine, _conn = _mock_engine(rows=[])
    repo = SessionDocumentRepository(engine)

    assert await repo.get_by_document("DOCMISSING", create_user="alice") is None


@pytest.mark.asyncio
async def test_list_by_user_filters_on_create_user():
    engine, conn = _mock_engine(rows=[_row()])
    repo = SessionDocumentRepository(engine)

    out = await repo.list_by_user("alice")

    assert len(out) == 1
    sql = _executed_sql(conn)
    assert "create_user = :create_user" in sql


# ---------------------------------------------------------------------------
# deletes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_by_document_returns_true_on_hit():
    engine, conn = _mock_engine(rowcount=1)
    repo = SessionDocumentRepository(engine)

    assert await repo.delete_by_document("DOCAAAAAAAAAAAAAAAAAAAAAA", create_user="alice") is True
    sql = _executed_sql(conn)
    assert "DELETE FROM session_documents" in sql
    assert "create_user = :create_user" in sql


@pytest.mark.asyncio
async def test_delete_by_document_returns_false_on_miss():
    engine, _conn = _mock_engine(rowcount=0)
    repo = SessionDocumentRepository(engine)

    assert await repo.delete_by_document("DOCMISSING", create_user="alice") is False


@pytest.mark.asyncio
async def test_delete_by_session_returns_document_ids_for_cascade():
    rows = [_row(), _row(document_id="DOCBBBBBBBBBBBBBBBBBBBBBB")]
    engine, conn = _mock_engine(rows=rows)
    repo = SessionDocumentRepository(engine)

    doc_ids = await repo.delete_by_session("thread-1")

    assert doc_ids == ["DOCAAAAAAAAAAAAAAAAAAAAAA", "DOCBBBBBBBBBBBBBBBBBBBBBB"]
    sql = _executed_sql(conn)
    assert "SELECT" in sql
    assert "DELETE FROM session_documents" in sql
    assert "session_id = :session_id" in sql
