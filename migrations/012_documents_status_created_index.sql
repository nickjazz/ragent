-- 012_documents_status_created_index.sql
-- Adds idx_status_created to cover count_by_statuses / list_by_statuses
-- queries that filter on (status, created_after) without source_app.
-- Without this index those queries fall back to idx_status_updated and
-- apply the created_at predicate as a residual filter on matched rows.
-- ALGORITHM=INPLACE, LOCK=NONE — no table rebuild on MariaDB 10.6.
--
-- IF NOT EXISTS: migrations/schema.sql already bakes this index into the
-- `documents` CREATE TABLE, and alembic/versions/000_squash.py re-reads
-- schema.sql on every fresh install — so this statement also runs (via the
-- chained 012 revision) against a DB that just got the index from squash.
-- Without IF NOT EXISTS that fresh-install path fails with a duplicate-key
-- error (docs/00_rule.md §Defensive DDL).
ALTER TABLE documents
  ADD INDEX IF NOT EXISTS idx_status_created (status, created_at),
  ALGORITHM=INPLACE, LOCK=NONE;
