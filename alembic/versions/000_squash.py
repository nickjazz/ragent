"""Squashed migration: full schema in one step (consolidates 001–012).

Revision ID: squash
Revises:
Create Date: 2026-05-20

Replaces the 001–012 migration chain. New installations apply this
single file; the resulting schema is identical to running 001→012 in
sequence with all field drift resolved:
  - source_meta VARCHAR(1024)  (was source_workspace VARCHAR(64) in 001;
                                renamed + widened in 005)
  - mime_type VARCHAR(128)     (was VARCHAR(64) in 004; widened in 007)
  - ingest_type ENUM includes 'upload'  (added in 011)
  - surrogate id PK            (added in 008)

Existing databases already at revision 012:
    alembic stamp squash
"""

from pathlib import Path

from alembic import op

revision = "squash"
down_revision = None
branch_labels = None
depends_on = None

_SQL = (Path(__file__).parent.parent.parent / "migrations" / "schema.sql").read_text(
    encoding="utf-8"
)


def _iter_statements(sql: str):
    """Strip -- comments first, then split on ;.

    Correct order prevents tearing a -- comment that embeds a semicolon
    (PR #86 pattern: naive split-then-strip leaves the post-; fragment
    without its -- prefix and feeds broken SQL to the engine).
    """
    lines: list[str] = []
    for ln in sql.splitlines():
        stripped = ln.lstrip()
        if stripped.startswith("--"):
            continue
        idx = ln.find("--")
        if idx >= 0:
            ln = ln[:idx].rstrip()
        lines.append(ln)
    for raw in "\n".join(lines).strip().split(";"):
        stmt = raw.strip()
        if stmt:
            yield stmt


def upgrade() -> None:
    """Apply schema.sql verbatim via exec_driver_sql to bypass SQLAlchemy's
    text() bind-param parser (the JSON_OBJECT seed payload contains ':' which
    text() would misinterpret as named bind parameters — same pattern as
    009_system_settings and 012_feedback wrappers)."""
    conn = op.get_bind()
    for stmt in _iter_statements(_SQL):
        conn.exec_driver_sql(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS feedback")
    op.execute("DROP TABLE IF EXISTS system_settings")
    op.execute("DROP TABLE IF EXISTS documents")
