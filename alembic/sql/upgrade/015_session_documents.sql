-- 015_session_documents.sql — chat-attachment redesign: attachments now ride
-- the standard ingest pipeline (documents + ES chunks_v1), so the bespoke
-- chat_attachments / chat_attachment_artifacts tables are dropped WITHOUT
-- data migration (explicit product decision — no backward compatibility for
-- the old attachment storage; docs/spec/decision_log.md).
--
-- session_documents links a chatagent session (twp-ai thread_id — same value,
-- see docs/spec/chatagent_v3.md) to the documents uploaded in it. Business
-- identity is the (session_id, document_id) tuple — a pure link table has no
-- standalone Base32 business id; uq_session_document is the business UNIQUE
-- required by docs/00_rule.md §Database Practices. No physical FK on
-- document_id (00_rule "No Physical Foreign Keys").
--
-- idx_session_created backs the session-wide listing ordered by upload time
-- (latest-first context injection); idx_document backs the reverse lookup on
-- DELETE /{attachmentId}; idx_create_user backs GET /mine.
CREATE TABLE IF NOT EXISTS session_documents (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_id  VARCHAR(64) NOT NULL,
  document_id CHAR(26)    NOT NULL,
  create_date DATETIME(6) NOT NULL,
  create_user VARCHAR(64) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_session_document (session_id, document_id),
  INDEX idx_session_created (session_id, create_date),
  INDEX idx_document (document_id),
  INDEX idx_create_user (create_user, session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Attachment uploads report AttachmentInfo.sizeBytes; documents rows created
-- by the attachment upload path persist the raw byte size here. NULL for
-- every other ingest path (inline/file), which never reads it back.
ALTER TABLE documents ADD COLUMN size_bytes BIGINT UNSIGNED NULL;

DROP TABLE IF EXISTS chat_attachment_artifacts;
DROP TABLE IF EXISTS chat_attachments;
