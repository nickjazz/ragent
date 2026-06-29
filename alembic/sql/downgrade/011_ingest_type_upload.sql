-- 011_ingest_type_upload.sql (downgrade) — drop the 'upload' discriminator.
-- Only safe if no row currently has ingest_type='upload' — same manual
-- backfill caveat as the upgrade's own comment; an operator must reconcile
-- those rows (e.g. to 'inline') before downgrading past this revision.
ALTER TABLE documents
  MODIFY COLUMN ingest_type ENUM('inline','file') NOT NULL DEFAULT 'inline',
  ALGORITHM=INSTANT;
