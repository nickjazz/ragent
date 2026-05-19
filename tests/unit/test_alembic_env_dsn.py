"""Alembic env.py must coerce the async aiomysql DSN to sync pymysql.

Without this coercion, `alembic upgrade head` raises
`sqlalchemy.exc.MissingGreenlet` because alembic drives a sync engine but
`MARIADB_DSN` is configured with the async aiomysql driver.
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

ENV_PY = Path(__file__).resolve().parents[2] / "alembic" / "env.py"


def _load_env_module(monkeypatch):
    """Import alembic/env.py without running migrations.

    env.py executes ``run_migrations_offline()`` at import; stub alembic.context
    to a no-op so the import succeeds without a live DB.
    """
    import alembic

    class _NullCM:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    noop = lambda *a, **kw: None  # noqa: E731
    stub = types.SimpleNamespace(
        config=types.SimpleNamespace(config_file_name=None),
        is_offline_mode=lambda: True,
        configure=noop,
        run_migrations=noop,
        begin_transaction=_NullCM,
    )
    monkeypatch.setattr(alembic, "context", stub)
    spec = importlib.util.spec_from_file_location("_alembic_env_under_test", ENV_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mysql+aiomysql://u:p@h:3306/db", "mysql+pymysql://u:p@h:3306/db"),
        ("mysql+pymysql://u:p@h:3306/db", "mysql+pymysql://u:p@h:3306/db"),
    ],
)
def test_sync_dsn_coerces_aiomysql_to_pymysql(monkeypatch, raw, expected):
    monkeypatch.setenv("MARIADB_DSN", raw)
    env = _load_env_module(monkeypatch)
    assert env._sync_dsn() == expected
