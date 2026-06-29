"""Add idx_status_created to documents.

Revision ID: 012
Revises: squash
Create Date: 2026-06-29

Reads migrations/012_documents_status_created_index.sql at runtime so this
wrapper and the raw SQL changelog file cannot drift apart (mirrors the
000_squash pattern).
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from ragent.bootstrap.init_schema import iter_statements

revision = "012"
down_revision = "squash"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parents[2] / "migrations" / "012_documents_status_created_index.sql"


def upgrade() -> None:
    conn = op.get_bind()
    for stmt in iter_statements(_SQL.read_text(encoding="utf-8")):
        conn.exec_driver_sql(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP INDEX IF EXISTS idx_status_created")
