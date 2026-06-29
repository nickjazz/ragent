-- 007_widen_mime_type.sql (downgrade) — narrow mime_type back to VARCHAR(64).
-- Only safe if no persisted value exceeds 64 chars (true for the formats
-- supported before DOCX/PPTX onboarding).
ALTER TABLE documents
  MODIFY COLUMN mime_type VARCHAR(64) NULL,
  ALGORITHM=INSTANT;
