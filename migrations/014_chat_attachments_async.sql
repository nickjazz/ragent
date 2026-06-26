-- 014_chat_attachments_async.sql — async worker processing for chat
-- attachments (T-CAT.W2, docs/spec/chat_attachments.md §7/§9).
--
-- `POST /chatagent/v3/attachments/upload` previously ran the full
-- pipeline → encrypt → persist sequence synchronously inside the HTTP
-- request, blocking an API-server thread for the unprotect + AST-build +
-- AES-GCM duration. The upload/process split now hands off to a TaskIQ
-- worker after storing raw bytes, mirroring the documents UPLOADED→PENDING
-- async-worker pattern; chat_attachments needs the equivalent in-flight
-- state plus the same failure diagnostics columns
-- (006_documents_error_code.sql is the precedent).
--
-- Appended at end of table: MariaDB 10.6 ALGORITHM=INSTANT only supports
-- appending. NULL allowed so the migration is online and pre-existing rows
-- need no backfill. error_reason bounded to 255 chars, same rationale as
-- documents.error_reason — full tracebacks live in worker logs only.
ALTER TABLE chat_attachments
  MODIFY COLUMN status ENUM('UPLOADED','PROCESSING','READY','FAILED') NOT NULL DEFAULT 'UPLOADED',
  ADD COLUMN error_code VARCHAR(64) NULL,
  ADD COLUMN error_reason VARCHAR(255) NULL,
  ALGORITHM=INSTANT;
