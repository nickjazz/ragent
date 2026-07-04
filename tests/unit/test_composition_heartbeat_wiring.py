"""T-ATTACH-R.0b — Container exposes heartbeat_tick and heartbeat_interval.

Verifies that:
1. Container dataclass declares `heartbeat_tick: Callable[[str], None]`
   and `heartbeat_interval: float` fields.
2. The tick callable built in build_container() executes
   `UPDATE documents SET updated_at=NOW(6) WHERE document_id=:id`
   via the sync engine (mock connection, begin() context manager).
3. heartbeat_interval defaults to 10.0 on the Container dataclass.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock


def test_container_has_heartbeat_fields() -> None:
    """Container dataclass must declare heartbeat_tick and heartbeat_interval."""
    from ragent.bootstrap.composition import Container

    field_names = {f.name for f in dataclasses.fields(Container)}
    assert "heartbeat_tick" in field_names, "Container must have heartbeat_tick field"
    assert "heartbeat_interval" in field_names, "Container must have heartbeat_interval field"


def test_container_heartbeat_interval_default() -> None:
    """heartbeat_interval defaults to 10.0 in the Container dataclass."""
    from ragent.bootstrap.composition import Container

    assert Container.heartbeat_interval == 10.0


def test_make_heartbeat_tick_executes_update_sql() -> None:
    """The tick callable must run UPDATE documents SET updated_at=NOW(6) via begin()."""
    from ragent.bootstrap.composition import _make_heartbeat_tick

    mock_conn = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_ctx

    tick = _make_heartbeat_tick(mock_engine)
    assert callable(tick)

    tick("doc-abc")

    mock_engine.begin.assert_called_once()
    mock_conn.execute.assert_called_once()
    executed_stmt, params = mock_conn.execute.call_args[0]
    assert "updated_at" in str(executed_stmt).lower()
    assert params == {"id": "doc-abc"}
