-- 016_documents_deleted.sql — audit table for deleted documents.
--
-- document_repository.delete() previously issued a hard DELETE on documents.
-- This migration introduces documents_deleted as an append-only audit log:
-- delete() now INSERT-SELECTs the row here (capturing deleted_at) inside the
-- same transaction, then hard-DELETEs from documents. The documents table
-- schema and all existing SELECT queries are untouched.
--
-- Design notes:
--   - status is VARCHAR(16) not ENUM: the audit table never drives a state
--     machine, and widening the live ENUM would require a paired migration here.
--   - document_id is PRIMARY KEY: a document is deleted at most once.
--   - error_reason is TEXT (vs VARCHAR(255) on documents): the audit copy
--     has no write-path size pressure, so TEXT avoids silent truncation.
--   - No physical FK on document_id (docs/00_rule.md "No Physical Foreign Keys").

CREATE TABLE IF NOT EXISTS documents_deleted (
  document_id  CHAR(26)      NOT NULL,
  create_user  VARCHAR(64)   NOT NULL,
  source_id    VARCHAR(128)  NOT NULL,
  source_app   VARCHAR(64)   NOT NULL,
  source_title VARCHAR(256)  NOT NULL,
  source_meta  VARCHAR(1024) NULL,
  source_url   VARCHAR(2048) NULL,
  object_key   VARCHAR(256)  NOT NULL,
  ingest_type  VARCHAR(16)   NOT NULL DEFAULT 'inline',
  minio_site   VARCHAR(64)   NULL,
  mime_type    VARCHAR(128)  NULL,
  size_bytes   BIGINT UNSIGNED NULL,
  status       VARCHAR(16)   NOT NULL,
  attempt      INT           NOT NULL DEFAULT 0,
  error_code   VARCHAR(64)   NULL,
  error_reason TEXT          NULL,
  created_at   DATETIME(6)   NOT NULL,
  updated_at   DATETIME(6)   NOT NULL,
  deleted_at   DATETIME(6)   NOT NULL,
  PRIMARY KEY (document_id),
  INDEX idx_deleted_at (deleted_at),
  INDEX idx_create_user (create_user)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
