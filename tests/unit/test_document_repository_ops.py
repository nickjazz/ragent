"""DocumentRepository: count_by_statuses() + list_by_statuses() — unit tests."""

import datetime
from unittest.mock import AsyncMock, MagicMock

from ragent.repositories.document_repository import DocumentRepository, DocumentRow


def _dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)


def _row(**kwargs) -> dict:
    base = dict(
        document_id="AAAAAAAAAAAAAAAAAAAAAAAAAA1",
        create_user="alice",
        source_id="DOC-1",
        source_app="confluence",
        source_title="My Title",
        source_meta=None,
        object_key="confluence_DOC-1_AAAA",
        status="FAILED",
        attempt=1,
        created_at=_dt("2026-01-01T00:00:00"),
        updated_at=_dt("2026-01-01T00:00:00"),
    )
    base.update(kwargs)
    return base


def _mock_engine(rows=None):
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows or []

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=result)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.begin = MagicMock(return_value=ctx)
    return engine, conn


# ---------------------------------------------------------------------------
# count_by_statuses
# ---------------------------------------------------------------------------


async def test_count_returns_status_map():
    engine, conn = _mock_engine(rows=[{"status": "FAILED", "cnt": 3}])
    repo = DocumentRepository(engine)

    result = await repo.count_by_statuses(["FAILED"])

    assert result == {"FAILED": 3}


async def test_count_multiple_statuses():
    engine, conn = _mock_engine(
        rows=[{"status": "FAILED", "cnt": 3}, {"status": "PENDING", "cnt": 2}]
    )
    repo = DocumentRepository(engine)

    result = await repo.count_by_statuses(["FAILED", "PENDING"])

    assert result == {"FAILED": 3, "PENDING": 2}


async def test_count_empty_result():
    engine, conn = _mock_engine(rows=[])
    repo = DocumentRepository(engine)

    result = await repo.count_by_statuses(["FAILED"])

    assert result == {}


async def test_count_filters_source_app():
    engine, conn = _mock_engine(rows=[{"status": "FAILED", "cnt": 1}])
    repo = DocumentRepository(engine)

    await repo.count_by_statuses(["FAILED"], source_app="myapp")

    executed_stmt = conn.execute.call_args[0][0]
    # bindparams wraps the text — check the underlying string
    sql = str(executed_stmt)
    assert "source_app" in sql


async def test_count_filters_created_after():
    engine, conn = _mock_engine(rows=[{"status": "FAILED", "cnt": 1}])
    repo = DocumentRepository(engine)

    await repo.count_by_statuses(["FAILED"], created_after=_dt("2026-06-01T00:00:00"))

    executed_stmt = conn.execute.call_args[0][0]
    sql = str(executed_stmt)
    assert "created_at" in sql


async def test_count_no_optional_filters_omits_extra_clauses():
    engine, conn = _mock_engine(rows=[])
    repo = DocumentRepository(engine)

    await repo.count_by_statuses(["UPLOADED"])

    executed_stmt = conn.execute.call_args[0][0]
    sql = str(executed_stmt)
    assert "source_app" not in sql
    assert "source_id" not in sql
    assert "created_at" not in sql


# ---------------------------------------------------------------------------
# list_by_statuses
# ---------------------------------------------------------------------------


async def test_list_order_asc():
    engine, conn = _mock_engine(rows=[_row()])
    repo = DocumentRepository(engine)

    await repo.list_by_statuses(["FAILED"])

    executed_stmt = conn.execute.call_args[0][0]
    sql = str(executed_stmt)
    assert "ORDER BY created_at ASC" in sql


async def test_list_limit_applied():
    engine, conn = _mock_engine(rows=[])
    repo = DocumentRepository(engine)

    await repo.list_by_statuses(["FAILED"], limit=42)

    executed_stmt = conn.execute.call_args[0][0]
    sql = str(executed_stmt)
    assert "LIMIT :limit" in sql
    params = conn.execute.call_args[0][1]
    assert params["limit"] == 42


async def test_list_default_limit_is_500():
    engine, conn = _mock_engine(rows=[])
    repo = DocumentRepository(engine)

    await repo.list_by_statuses(["FAILED"])

    params = conn.execute.call_args[0][1]
    assert params["limit"] == 500


async def test_list_filters_combined():
    engine, conn = _mock_engine(rows=[])
    repo = DocumentRepository(engine)

    await repo.list_by_statuses(
        ["FAILED", "PENDING"],
        source_app="app1",
        source_id="s1",
        created_after=_dt("2026-06-01T00:00:00"),
    )

    executed_stmt = conn.execute.call_args[0][0]
    sql = str(executed_stmt)
    assert "source_app" in sql
    assert "source_id" in sql
    assert "created_at" in sql


async def test_list_empty_result():
    engine, conn = _mock_engine(rows=[])
    repo = DocumentRepository(engine)

    result = await repo.list_by_statuses(["FAILED"])

    assert result == []


async def test_list_returns_document_rows():
    engine, conn = _mock_engine(rows=[_row(status="FAILED")])
    repo = DocumentRepository(engine)

    result = await repo.list_by_statuses(["FAILED"])

    assert len(result) == 1
    assert isinstance(result[0], DocumentRow)
    assert result[0].status == "FAILED"
