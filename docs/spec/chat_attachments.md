# chat_attachments — In-Conversation File Attachments

> Part of [docs/00_spec.md §3.4.9](../00_spec.md#349-post-chatagentattachmentsupload-get-chatagentattachments--in-conversation-file-attachments). Standard: [docs/00_rule.md](../00_rule.md).

---

## 1. Goal

A user attaches a file inside a `/chatagent/v3` conversation; the agent can
reference its content on the current turn and on every later turn, across all
three message-reconstruction paths (live POST, Redis reconnect, session
history).

## 2. MIME allow-list

Reuses the same six formats as ingest (`AttachmentMime` enum, schema-isolated
from `IngestMime` so the two domains can evolve independently even though the
values match today):

```
text/plain, text/markdown, text/html,
application/vnd.openxmlformats-officedocument.wordprocessingml.document,  (docx)
application/vnd.openxmlformats-officedocument.presentationml.presentation,  (pptx)
application/pdf
```

Extension fallback applies when the browser-supplied `Content-Type` is
generic/incorrect (`MIME_EXTENSIONS` mapping, shared util). Rejection →
`ATTACHMENT_MIME_UNSUPPORTED` (415) / `ATTACHMENT_TOO_LARGE` (413).

## 3. Unprotect whitelist

Not every MIME needs the external unprotect round-trip. Only binary formats
that can carry DRM/IRM wrapping go through it:

```python
UNPROTECT_MIMES = frozenset({
    AttachmentMime.PDF,
    AttachmentMime.DOCX,
    AttachmentMime.PPTX,
})
```

`text/plain` / `text/markdown` / `text/html` skip the call entirely — there is
no protection format for plain text, so calling the external API would be
pure waste. Skipped when `mime not in UNPROTECT_MIMES`, when no
`unprotect_client` is wired, or (fail-soft) when the call raises — original
bytes are used as a fallback in all three cases; the `chat_attachment`
pipeline never blocks on unprotect.

## 4. `chat_attachment` pipeline

`src/ragent/pipelines/chat_attachment/` — **load → optional unprotect → AST
build**. Reuses the existing `_MimeAwareSplitter` family
(`pipelines/ingest/splitter.py`) for the AST-building step; this pipeline
adds no new per-format parsing logic. Two AST variants are produced per
attachment:

- **complete** — full structural AST (same shape the ingest splitter already
  produces for the format).
- **simplified** — title + first two lines per section; **derived from the
  complete AST in memory** (a tree walk, not a second parse) — the document
  is parsed exactly once per attachment.

The pipeline's only responsibility is producing plaintext AST JSON. It does
**not** encrypt and does **not** persist — those are the caller's (service
layer's) responsibility (see §5, §6) per SRP: a unit test can assert the
pipeline's output without touching a key manager or MinIO.

## 5. AST encryption (KEK/DEK)

Both AST variants are encrypted before being written to storage.

**Key model** — one process-wide DEK, not per-artifact:

- `RAGENT_KEK_BASE64` — base64 KEK (32 bytes), injected at process start.
- `RAGENT_ENCRYPTED_DEK_BASE64` — the DEK, AES-Key-Wrapped under the KEK,
  generated offline, injected at process start.
- `KeyManager.from_env()` unwraps the DEK exactly once at startup
  (`security/key_manager.py`); the DEK lives in memory for the process
  lifetime. No per-artifact key generation, no `encrypted_dek` field stored
  alongside each artifact.
- **KEK rotation**: re-wrap the *same* DEK under the new KEK offline, update
  both env vars, restart. The DEK itself never changes, so no re-encryption
  of existing artifacts is needed.

**Cipher** — AES-256-GCM, one random 12-byte nonce per artifact
(`security/ast_cipher.py`). Storage envelope:

```json
{
  "version": "1.0",
  "algorithm": "AES-256-GCM",
  "nonce": "<hex>",
  "ciphertext": "<hex, GCM tag included>",
  "metadata": {
    "attachment_id": "att_xxx",
    "ast_type": "complete" | "simplified",
    "created_at": "2026-06-25T10:30:00Z"
  }
}
```

`ASTCipher` only depends on `KeyManager.dek` (Interface Segregation — it never
sees the KEK or the wrap/unwrap mechanics). `DocumentArtifactResolver`
decrypts on read, before the AST re-enters the chat context.

## 6. Storage

`storage/document_store.py::DocumentStore` — a narrow Protocol
(`put`/`get`/`delete`/`exists`) so the chat-attachment service depends on an
abstraction, not directly on MinIO (Dependency Inversion). `MinIODocumentStore`
is the only implementation today; built once in `bootstrap/composition.py`
and injected.

## 7. Persistence & reconstruction paths

Attachment metadata (filename, MIME, size, `attachment_id`) is rendered into
an `<attachments>` block inside the same `<hidden>` preamble `/chatagent/v3`
already uses for `<context>`/`<state>` (§3.4.7) — no new wrapper concept, no
`run_id` indirection (the block is bound to the user turn it's attached to,
the same way `<hidden>` already is):

```
<hidden>
<attachments>[{"attachmentId": "att_xxx", "filename": "report.pdf", "mimeType": "application/pdf", "sizeBytes": 12345}]</attachments>
<context>...</context>
</hidden>

{user message}
```

Two paths need attachment-specific code; a third needs none:

1. **Live POST** — `/chatagent/v3` resolves `attachment_ids` → builds the
   `<attachments>` block → folds it into the outbound `inputData.message`
   exactly like `<context>`/`<state>` already are. This happens before the
   producer thread starts, so it requires no new persistence of its own.
2. **Redis reconnect — no code change.** `ChatStreamStore` (§3.4.7) only
   tees the upstream's *response* SSE frames (`XADD` per frame); it never
   buffers the request. The `<attachments>` block lives solely in the
   outbound request built in path 1 above, and the response stream never
   echoes `<hidden>` content back (§3.4.7 "No `<hidden>` stripping on the
   stream"). So a reconnect — which only replays the already-buffered
   response frames via `XRANGE` — carries attachments correctly with zero
   attachment-aware code; the existing resumable-stream mechanism is
   already content-agnostic.
3. **Session history** — `services/chatagent_session.py` gains
   `_extract_attachments_from_hidden()`, which **must run before**
   `utility/hidden.py::strip_machine_context()` — that helper removes the
   entire `<hidden>…</hidden>` block (it doesn't single out `<attachments>`),
   so the extraction step has to read the block first; `strip_machine_context`
   then deletes the whole wrapper from the rendered text exactly as it does
   today for `<context>`/`<state>`.

No thread-ownership check is performed on attachment reads — identical to the
existing chat-session trust model; isolation comes from the `create_user`
column on `chat_attachments` plus the query predicate, not from an
authorization check.

## 8. Error codes

| Code | HTTP | Trigger |
|---|---|---|
| `ATTACHMENT_MIME_UNSUPPORTED` | 415 | MIME not in `AttachmentMime` allow-list (after extension fallback) |
| `ATTACHMENT_TOO_LARGE` | 413 | size exceeds cap |
| `ATTACHMENT_PARSE_FAILED` | 422 | `chat_attachment` pipeline raised during AST build |

## 9. DB schema (`013_chat_attachments.sql`)

`chat_attachments` (id, thread_id, create_user, filename, mime_type,
size_bytes, status, created_at) + `chat_attachment_artifacts` (attachment_id
FK, ast_type, storage_key, created_at). No `introduced_run_id` column — the
`<hidden>` block already binds the attachment to its turn.
