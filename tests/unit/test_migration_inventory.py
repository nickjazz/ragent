"""Static guards for the migration inventory.

The Docker-backed schema drift test proves the resulting DB shape. These tests
catch cheaper bookkeeping mistakes before a container is needed: missing raw
SQL revisions, broken numbering, and stale Alembic squash comments.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
MIGRATIONS = ROOT / "migrations"
ALEMBIC_SQUASH = ROOT / "alembic" / "versions" / "000_squash.py"


def test_numbered_sql_migrations_are_contiguous() -> None:
    migration_numbers = sorted(
        int(match.group(1))
        for path in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")
        if (match := re.match(r"^(\d{3})_", path.name))
    )

    assert migration_numbers, "expected at least one numbered SQL migration"
    assert migration_numbers == list(range(1, migration_numbers[-1] + 1)), (
        "numbered SQL migrations must be contiguous; missing revision(s) would "
        "make deployment history ambiguous"
    )


def test_schema_snapshot_mentions_latest_numbered_migration() -> None:
    latest = max(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    schema_sql = (MIGRATIONS / "schema.sql").read_text(encoding="utf-8")

    assert latest.name in schema_sql, (
        "migrations/schema.sql should document the latest raw SQL migration it "
        f"folds in; missing {latest.name} suggests the snapshot comment is stale"
    )


def test_alembic_squash_reads_schema_snapshot() -> None:
    squash = ALEMBIC_SQUASH.read_text(encoding="utf-8")

    assert "migrations/schema.sql" in squash
    assert "schema.sql" in squash and "read_text" in squash, (
        "the Alembic squash revision must apply the schema snapshot, not drift "
        "into a separate hand-written DDL path"
    )
