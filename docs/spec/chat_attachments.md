# chat_attachments — In-Conversation File Attachments (Zero-Trust Architecture)

> Part of [docs/00_spec.md §3.4.9](../00_spec.md#349-post-chatagentv3attachments--in-conversation-file-attachments). Standard: [docs/00_rule.md](../00_rule.md).

---

## 1. Goal

A user attaches a file inside a `/chatagent/v3` conversation.  The agent
receives only the file **metadata** (`documentId`, `filename`, `uploadedAt`)
in its context and must call the `/mcp/v1` `retrieve` tool to read file
content.  The attachment is indexed through the standard ingest pipeline so
all access paths (live POST, Redis reconnect, session history) see a
consistent view without special-casing.

### 1.1 Use Cases

**UC1 — Upload & background processing.**
As a user I attach a file; the UI receives a `document_id` immediately (`202`)
and polls `GET .../attachments/{id}` until `READY`, without blocking the chat.

**UC2 — Reference an attachment in a turn.**
Once `READY`, the agent sees a JSON metadata list in `<attachments>` inside
`<hidden>`.  To answer questions about the file the agent calls the `/mcp/v1`
`retrieve` tool with the `documentId`(s) from the list; the endpoint enforces
Anti-IDOR so only the owning user can retrieve those chunks.

**UC3 — Cross-turn and session-fallback.**
When the user sends a new turn **without** explicit `attachmentIds`, the
resolver looks up all documents previously uploaded in the session
(`session_documents`) and injects them as the fallback list, marked newest-first
with `"latest": true` on the most recent.  The instruction tells the agent to
retrieve the latest first, then expand as needed.

**UC4 — Browse & delete.**
`GET .../attachments?threadId=` lists a session's attachments.
`GET .../attachments/mine` lists all attachments for the caller, across sessions.
`DELETE .../attachments/{id}` removes the document from ES + DB.
`DELETE /chatagent/v3/session` cascades deletion for the whole session.

### 1.2 Architecture

```
Upload request
  ├── AttachmentIngestService.upload()
  │     ├── IngestService.create()          → UPLOADED row + MinIO stage
  │     ├── SessionDocumentRepository.create(session_id, document_id, user)
  │     └── kiq("ingest.pipeline", document_id)
  └── 202 { document_id }   ← this is the attachmentId

Worker (ingest.pipeline)
  ├── standard pipeline (load → split → embed → ES chunks_v1)
  └── documents.status = READY

Chat turn (POST /chatagent/v3)
  ├── AttachmentContextResolver.resolve(session_id, user_id, attachment_ids?)
  │     ├── explicit ids  → scope-check against session_documents
  │     └── fallback      → list_by_session(), newest-first, latest=true marker
  └── <hidden>…<attachments>[…]</attachments><instruction>…</instruction>…</hidden>

Agent calls /mcp/v1 (retrieve tool)
  ├── RetrieveV2Service.assert_owner(user_id, document_id_list)
  ├── run_retrieval(pipeline, filters={"field":"document_id","operator":"in","value":[…]})
  └── returns chunks — only from caller-owned documents
```

---

## 2. Supported MIME Types

`AttachmentMime` is a strict subset of `IngestMime`; CSV is **not** accepted
for attachments (no `AttachmentMime.CSV`).

| MIME | Extension(s) | Ingest splitter used |
|---|---|---|
| `text/plain` | `.txt` | `DocumentSplitter` |
| `text/markdown` | `.md` | `_MarkdownASTSplitter` |
| `text/html` | `.html` | `_HtmlASTSplitter` |
| `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `.docx` | `_DocxASTSplitter` |
| `application/vnd.openxmlformats-officedocument.presentationml.presentation` | `.pptx` | `_PptxASTSplitter` |
| `application/pdf` | `.pdf` | `_PdfASTSplitter` |

Unsupported MIME → `415 ATTACHMENT_MIME_UNSUPPORTED`.

---

## 3. Ingest Integration

`AttachmentIngestService.upload()` calls `IngestService.create()` with:

| Field | Value |
|---|---|
| `source_app` | `"chat_attachment"` (= `ATTACHMENT_SOURCE_APP`) |
| `source_id` | `new_id()` — unique ULID per upload; no supersede triggered |
| `source_title` | original filename |
| `source_meta` | `thread_id` |
| `size_bytes` | `len(file_bytes)` |

The resulting `document_id` (26-char ULID) is returned as the `attachmentId`.

Corpus-wide endpoints (`/retrieve/v1`, `/chat/v1`, `/mcp/v1`) exclude
`source_app = "chat_attachment"` via `build_attachment_exclusion_filter()`.

---

## 4. `session_documents` Link Table

Links sessions to their documents. No physical foreign keys (00_rule §No Physical
Foreign Keys).

```sql
CREATE TABLE session_documents (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  session_id  VARCHAR(64)  NOT NULL,
  document_id CHAR(26)     NOT NULL,
  create_date DATETIME(6)  NOT NULL,
  create_user VARCHAR(64)  NOT NULL,
  UNIQUE KEY uq_session_document (session_id, document_id),
  INDEX idx_session_created (session_id, create_date),
  INDEX idx_document (document_id),
  INDEX idx_create_user (create_user, session_id)
) ENGINE=InnoDB CHARSET=utf8mb4;
```

`SessionDocumentRepository` methods: `create` (upsert-idempotent), `list_by_session`,
`get_by_document`, `list_by_user`, `delete_by_document`, `delete_by_session`.

---

## 5. Authentication — Fail-Closed

All attachment endpoints and `/retrieve/v2` / `/mcp/v1` reject unauthenticated
callers with `403 AUTH_REQUIRED`.  The old `user_id or "anonymous"` fallback
is removed.  Rationale: an anonymous upload under the Anti-IDOR model is a
dead document that can never be retrieved.

---

## 6. Status Mapping

The attachment surface exposes four status values derived from
`documents.status`:

| `documents.status` | Attachment `status` |
|---|---|
| `PENDING`, `UPLOADED` | `PROCESSING` |
| `READY` | `READY` |
| `FAILED` | `FAILED` |
| `DELETING` | `PROCESSING` |

---

## 7. `/retrieve/v2` — Document-Scoped Retrieval

`POST /retrieve/v2` requires `document_id_list` (1–100 ids) and performs
Anti-IDOR ownership validation before retrieval.

```
POST /retrieve/v2
  ├── RetrieveV2Service.assert_owner(user_id, document_id_list)
  │     ├── document_repo.get_create_users_by_document_ids(ids)
  │     └── any id unknown or create_user ≠ user_id → raise → 403 DOCUMENT_FORBIDDEN
  └── run_retrieval(pipeline, filters=build_document_id_filter(ids))
        └── post-filter: remove chunks whose meta.document_id ∉ allowed set
```

**Error codes:**

| Condition | Code | HTTP |
|---|---|---|
| Any id unknown or not owned by caller | `DOCUMENT_FORBIDDEN` | 403 |
| `user_id` is `None` | `DOCUMENT_FORBIDDEN` | 403 |
| `document_id_list` absent/empty/> 100 | — | 422 |

---

## 8. `/mcp/v1` — Attachment-Scoped MCP Tool

`POST /mcp/v1` exposes a single `retrieve` tool with `document_id_list` as a
required input field (1–100 ids, `minItems: 1`).  The handler reuses
`RetrieveV2Service.assert_owner`; IDOR violations return JSON-RPC error
`{code: -32002, data: {error_code: "DOCUMENT_FORBIDDEN"}}`.

The tool description instructs the model to supply `documentId` values from
the `<attachments>` block.  It notes that files may still be in `PROCESSING`
status and the model should inform the user if no chunks are returned.

---

## 9. `<attachments>` Metadata Block

`AttachmentContextResolver.resolve()` injects a JSON array into the `<hidden>`
preamble before each `/chatagent/v3` turn.  If the session has no uploaded
files and no explicit `attachmentIds` were provided, it returns `None` and no
`<attachments>` block is injected.

### 9.1 JSON Shape

```json
[
  {
    "documentId": "01J...",
    "attachmentId": "01J...",
    "filename": "report.pdf",
    "uploadedAt": "2026-07-03T10:00:00Z",
    "latest": true
  }
]
```

`documentId` and `attachmentId` carry the same value (both emitted for
forward-compatibility with frontends that render `attachmentId`).  `latest:
true` marks the most-recently-uploaded document in the session-fallback path;
only one entry carries `latest: true`.

### 9.2 Instructions

Two differentiated instruction variants are appended after `</attachments>`:

**Explicit ids (user attached files to this turn):**
```
[Instruction] The user attached the files listed above to this message.
Do not guess their content. Before answering any question about them,
you MUST call the retrieve tool with document_id_list containing ALL
the documentId values listed above.
```

**Session fallback (files from earlier in the conversation):**
```
[Instruction] The files listed above were previously uploaded in this
conversation. Do not guess their content. For any question about these
files, FIRST call the retrieve tool with the documentId marked "latest": true.
If the retrieved chunks are insufficient to answer, retrieve the remaining
documentId values as needed.
```

The `<instruction>` tag is stripped by `strip_machine_context` on session
history reads (same mechanism as `<hidden>` stripping).

---

## 10. API Endpoints

All endpoints nest under `/chatagent/v3/attachments/`.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/chatagent/v3/attachments/upload` | `X-User-Id` (required) | Upload file. `multipart/form-data` with `threadId` + `file`. Returns `202 { document_id }`. |
| GET | `/chatagent/v3/attachments` | `X-User-Id` | List attachments for `?threadId=`. Returns `{ items: [AttachmentInfo] }`. |
| GET | `/chatagent/v3/attachments/{id}` | `X-User-Id` | Get single attachment status. `404 ATTACHMENT_NOT_FOUND` if unknown or not owned. |
| GET | `/chatagent/v3/attachments/mine` | `X-User-Id` | List all caller attachments across sessions. |
| DELETE | `/chatagent/v3/attachments/{id}` | `X-User-Id` | Delete attachment + cascade ES chunks. `404 ATTACHMENT_NOT_FOUND` if unknown or not owned. |

**`AttachmentInfo` shape:**

```json
{
  "attachmentId": "01J...",
  "filename": "report.pdf",
  "mimeType": "application/pdf",
  "status": "READY",
  "sizeBytes": 204800,
  "errorCode": null,
  "errorReason": null,
  "createdAt": "2026-07-03T10:00:00Z"
}
```

Unauthenticated → `403 AUTH_REQUIRED`.

---

## 11. Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ATTACHMENT_MAX_SIZE_BYTES` | `52428800` (50 MB) | Maximum file size accepted per upload. |
| `ATTACHMENT_MAX_FILES` | `10` | Maximum `attachmentIds` per `/chatagent/v3` turn. |

See [`docs/spec/env_vars.md`](env_vars.md) §4.6.6 for full listing.

---

## 12. Migration

Migration `015_session_documents.sql`:
- Drops `chat_attachments` and `chat_attachment_artifacts` tables.
- Creates `session_documents`.
- Adds `documents.size_bytes BIGINT UNSIGNED NULL`.

No data migration — the old attachment tables are dropped outright.  Old
sessions that referenced AST artifacts lose attachment context but continue to
function as plain chat sessions.

---

## 13. Deleted Components

The following modules were removed in Track T-CAT.R1/R2:

| Removed | Replaced by |
|---|---|
| `services/chat_attachment_service.py` | `services/attachment_ingest_service.py` |
| `services/document_artifact_resolver.py` | `services/attachment_context_resolver.py` |
| `workers/attachment.py` | standard `ingest.pipeline` worker |
| `pipelines/chat_attachment/` | standard `pipelines/ingest/` pipeline |
| `repositories/attachment_repository.py` | `repositories/session_document_repository.py` |
| `security/key_manager.py`, `security/ast_cipher.py` | (deleted — no encryption needed) |
| `scripts/gen_attachment_keys.py`, `scripts/decrypt_artifact.py` | (deleted) |
| `RAGENT_KEK_BASE64`, `RAGENT_ENCRYPTED_DEK_BASE64` env vars | (removed) |
