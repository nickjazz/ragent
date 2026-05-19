# 00_spec.md — Distributed RAG Agent

> Source: `docs/draft.md` · Standard: `docs/00_rule.md`

---

## 1. Mission

- Enterprise internal knowledge retrieval backend.
- Streaming chat answers grounded in private documents.
- Pluggable extractor architecture: graph reasoning (P3) without pipeline rewrite.

### ⚠️ P1 OPEN Mode
- Authentication **DISABLED** in P1. `X-User-Id` header trusted; recorded as `documents.create_user` (audit only, not authorization). JWT restored in **P2**.
- Permission gating **DISABLED** in P1. The Permission Layer (§3.5) ships in **P2**, backed by OpenFGA, and stays out of the retrieval/ES path.
- Startup guard refuses to start unless `RAGENT_AUTH_DISABLED=true AND RAGENT_ENV=dev`.

---

## 2. Phase 1 Scope

| In P1 | Deferred |
|---|---|
| Ingest CRUD (Create / Read / List / Delete) with cascade | JWT → P2 |
| Indexing Pipeline (§3.2) + Chat Pipeline (§3.4) | AsyncPipeline → P2 |
| Plugin Protocol v1, VectorExtractor, StubGraphExtractor | GraphExtractor → P3 |
| Third-party clients: Embedding, LLM, Rerank, TokenManager | Rerank wiring → P2 |
| Reconciler + locking | MCP real handler → P2 |
| Observability: OTEL auto-trace | — |

---

## 3. Domains

### 3.1 Ingest Lifecycle

> **v2 OVERRIDE (2026-05-06)** — see `docs/team/2026_05_06_ingest_api_v2.md` for the full team decision; the operative contract below supersedes the v1 multipart description further down.
>
> **API:** `POST /ingest` is **JSON only** (no multipart). Body discriminator `ingest_type ∈ {inline, file}`:
> - `inline` → `{ingest_type, mime_type, content: str, source_id, source_app, source_title, source_meta?, source_url?}`. `content` is UTF-8; size ceiling `INGEST_INLINE_MAX_BYTES` (default 10 MB) on the encoded byte length. API stages the content to MinIO `__default__` site.
> - `file` → `{ingest_type, mime_type, minio_site, object_key, source_id, source_app, source_title, source_meta?, source_url?}`. API HEAD-probes `(minio_site, object_key)`; absent → 422 `INGEST_OBJECT_NOT_FOUND`; size > `INGEST_FILE_MAX_BYTES` (50 MB) → 413. **No copy** — worker reads from caller's bucket.
>
> **MIME allow-list:** `text/plain`, `text/markdown`, `text/html`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document` (DOCX), `application/vnd.openxmlformats-officedocument.presentationml.presentation` (PPTX), `application/pdf` (PDF). Anything else → 415 `INGEST_MIME_UNSUPPORTED`. **CSV is dropped** (was v1).
>
> **Source columns:** `source_id`, `source_app`, `source_title`, `source_meta?` (free-format ≤ 1024 chars; renamed from `source_workspace` per B35) **plus new `source_url`** (opaque ≤ 2048 chars, display-only in citations).
>
> **MinIO multi-site:** server reads `MINIO_SITES` JSON env at boot → `MinioSiteRegistry`. `__default__` is mandatory (used by inline). Sites with `read_only=true` refuse post-READY delete (`file` ingests against caller-owned buckets).
>
> **Cleanup branching by `documents.ingest_type`:**
> | `ingest_type` | Entry path | Worker reads from | Post-READY auto-delete | Reclaimed by `DELETE /ingest/v1/{id}` |
> |---|---|---|---|---|
> | `inline` | `POST /ingest/v1` JSON body (UTF-8 text only — binary MIMEs rejected at schema layer) | `__default__/<server-built object_key>` | **yes** | yes if status pre-READY (post-READY the blob is already gone) |
> | `file`   | `POST /ingest/v1` JSON body (caller-supplied `minio_site` + `object_key`) | caller's `(minio_site, object_key)` | **no** (object isn't ours) | **no** (caller-owned) |
> | `upload` | `POST /ingest/v1/upload` multipart (server stages binary file bytes) | `__default__/<server-built object_key>` | **no** (the only path that reclaims is explicit DELETE) | **yes** at any status |
>
> **Storage model (revised):** chunks live **only** in ES `chunks_v1`. The MariaDB `chunks` table is **dropped**. `documents` keeps metadata. Two stores total: `documents` (MariaDB, metadata) + `chunks_v1` (ES, content + embedding + raw_content).
>
> **Per-step structured logging (Logging Rule extension):** every pipeline component emits `event=ingest.step.{started,ok,failed}` with `{document_id, step, mime_type, duration_ms, error_code?, error?}`. Failures map to a small enum (`PIPELINE_UNROUTABLE`, `EMBEDDER_ERROR`, `ES_WRITE_ERROR`, `PIPELINE_TIMEOUT`). Happy path emits `event=ingest.ready` with chunk count. This is how operators answer "which doc, which step, why failed" from logs alone.
>
> **State machine, locking, heartbeat, supersede, reconciler arms — unchanged from v1.** Only the ingress and pipeline interior change.

**State machine:** `UPLOADED → PENDING → READY | FAILED`; `DELETING` transient on delete.

**Locking discipline:**
- Status mutations use **atomic conditional UPDATE** with rowcount-based dispatch — no `SELECT FOR UPDATE [NOWAIT]` on the documents row-mutation paths (`claim_for_processing`, `claim_for_deletion`, `update_status`, `promote_to_ready_and_demote_siblings`). The canonical idiom is `UPDATE documents SET status=:to_status, … WHERE document_id=:id AND status IN (:accept_set)`; `rowcount=1` means the caller won the transition, `rowcount=0` means another writer beat us (or the row is gone) and the caller no-ops gracefully. InnoDB's row-level X-lock during the UPDATE statement serialises concurrent writers — the lock window is microseconds, not the multi-statement span of the old `SELECT FOR UPDATE` pattern. (The reconciler stale-sweep and supersede single-loser-per-tx scan retain `SKIP LOCKED` semantics — those are bulk load-shedding scans across rows, not single-row transitions.)
- Pipeline body runs **outside any DB transaction**: no row locks are held while external calls (embedder, ES, plugins, MinIO) run. The worker's claim (TX-A) is a single atomic UPDATE that flips `UPLOADED|PENDING → PENDING` and bumps `attempt`; the terminal commit (TX-B) is another single atomic UPDATE that flips `PENDING → READY|FAILED`. Heartbeat keeps the `PENDING` row warm; the Reconciler's stale-sweep is governed entirely by `updated_at`, not by row locks.
- `update_status` and the claim path both validate transitions via the WHERE clause's accept-set; an invalid transition surfaces as `rowcount=0` (no exception) on the claim path, and as `IllegalStateTransition` (raised in code on rowcount=0) on the `update_status` path — preserving the v1 error contract for terminal writes.

**Worker heartbeat (B16) — closes the no-lock-window race:** because the pipeline body holds no row lock, a naive Reconciler "PENDING > 5 min" sweep would happily re-dispatch a still-running worker and produce double processing. The worker therefore **updates `documents.updated_at = NOW()` every 30 s** during the pipeline body (background timer; one cheap PK-keyed `UPDATE`). The Reconciler's threshold becomes `WHERE status='PENDING' AND updated_at < NOW() - INTERVAL 5 MINUTE` — only **stale-heartbeat** rows are re-dispatched. Heartbeat interval is configured via `WORKER_HEARTBEAT_INTERVAL_SECONDS` (default 30).

**Per-document pipeline timeout (B18):** the worker enforces an overall ceiling of `PIPELINE_TIMEOUT_SECONDS` (default 1800 s = 30 min) around the pipeline body. On overrun, the worker transitions the row to `FAILED` with `error_code=PIPELINE_TIMEOUT` and runs the §3.1 R5 cleanup path (`fan_out_delete` + `delete_by_document_id`). This bounds pathological-document worst case so the Reconciler never sees an infinitely-running worker (heartbeat would also catch it after 5 min, but the ceiling makes the failure deterministic).

**Storage model:** MinIO is **transient staging only** — needed because Router (API) and Worker may run on different hosts. The original file is deleted from MinIO after the pipeline reaches a terminal state (`READY` or `FAILED`). After ingest, only chunks (ES) + metadata (MariaDB) remain.

**Object key convention (B10):** `{source_app}_{source_id}_{document_id}` (single bucket from `MINIO_BUCKET` env, default `ragent`). `source_app` and `source_id` are sanitized to `[A-Za-z0-9._-]` (other chars percent-encoded) to satisfy MinIO key constraints. The `document_id` suffix guarantees uniqueness even when the same `(source_app, source_id)` is re-POSTed before supersede converges.

**Pipeline retry idempotency:** Every pipeline run begins with `ChunkRepository.delete_by_document_id(document_id)` and `VectorExtractor.delete(document_id)` (idempotent ES bulk-delete) so a Reconciler retry of a partially-written attempt does not produce duplicate chunks. `chunk_id` may therefore be a fresh `new_id()` per run; identity is by `(document_id, ord)`.

**Supersede model (smart upsert):** Every `POST /ingest` carries a mandatory `(source_id, source_app, source_title)` triple (e.g. `("DOC-123", "confluence", "Q3 OKR Planning")`) and an optional `source_meta` (free-format ≤ 1024 chars). The `(source_id, source_app)` pair is the **logical identity** of a document; `source_title` is human-readable display text required by chat retrieval (`sources[].title` in §3.4). At steady state at most one `READY` row may exist per `(source_id, source_app)`. A new POST always creates a fresh `document_id`; when it reaches `READY`, the system enqueues a **supersede** task that selects every `READY` row sharing the same `(source_id, source_app)`, keeps the one with `MAX(created_at)`, and cascade-deletes the rest. This guarantees "latest write wins" even when documents finish out-of-order, gives zero-downtime replacement (old chunks remain queryable until the new ones are indexed), and preserves the old version if the new ingest fails. Supersede is enqueued **only** on the `PENDING → READY` transition; FAILED or mid-flight DELETE never triggers it. Uniqueness is **eventual**, enforced by supersede — not by a DB UNIQUE constraint, since transient duplicates are expected during ingestion. **Mutation = re-POST with the same `(source_id, source_app)` (and updated `source_title` if the title changed); there is no PUT/PATCH endpoint.**

**Create flow:**
1. `POST /ingest` (`source_id`, `source_app`, `source_title`, optional `source_meta`) → MIME/size validation → MinIO upload (staging) → `documents(UPLOADED, source_id, source_app, source_title, source_meta)` → kiq `ingest.pipeline` → 202.
2. Worker `ingest.pipeline`:
   - **TX-A (atomic claim):** single statement `UPDATE documents SET status='PENDING', attempt=attempt+1, updated_at=NOW(6) WHERE document_id=:id AND status IN ('UPLOADED','PENDING')`. `rowcount=1` → proceed; `rowcount=0` → log `event=ingest.claim_skipped` and exit gracefully (row is already READY/FAILED/DELETING or missing). The `PENDING` source state in the accept-set is what makes reconciler redispatch and manual rerun (§3.1.x) safe.
   - **Heartbeat (B16):** start a background timer that issues `UPDATE documents SET updated_at=NOW() WHERE document_id=?` every `WORKER_HEARTBEAT_INTERVAL_SECONDS` (default 30) for the lifetime of the pipeline body. Cancelled in `finally` before TX-B.
   - **Pipeline body (no DB tx, wall-clock-bounded by `PIPELINE_TIMEOUT_SECONDS`, default 1800):** `delete_by_document_id` (idempotency) → run §3.2 → `fan_out` → all required ok ⇒ outcome=`READY`; any required error ⇒ outcome=`PENDING_RETRY` (no terminal commit; Reconciler resumes); attempt > 5 ⇒ outcome=`FAILED`; ceiling exceeded ⇒ outcome=`FAILED` with `error_code=PIPELINE_TIMEOUT` (B18).
   - **TX-B (terminal only):** commit `READY` or `FAILED`. **On `FAILED`, also call `fan_out_delete` + `delete_by_document_id` to clean partial output before commit.**
   - **Post-commit (best-effort, no tx):** `MinIOClient.delete_object` (errors swallowed → log `event=minio.orphan_object`); on `READY`, kiq `ingest.supersede(document_id)` (idempotent).
3. Worker `ingest.supersede`:
   - **Single-loser-per-tx:** in a loop, `SELECT 1 row` matching `(source_id, source_app, status='READY')` ordered ASC by `created_at` (i.e. the oldest non-survivor) using `FOR UPDATE SKIP LOCKED` → cascade-delete that row → commit → repeat. Survivor = whichever row remains last; query naturally stops when only one `READY` row is left for the pair. **Avoids holding K row-locks across K cascades.**
   - Naturally idempotent: re-delivery finds ≤ 1 row and no-ops.

**Delete flow:**
1. `DELETE /ingest/{id}` → atomic claim `UPDATE … SET status='DELETING' WHERE document_id=:id AND status IN ('UPLOADED','PENDING','READY','FAILED')`. `rowcount=1` → outside-tx cascade (`fan_out_delete` → `delete_by_document_id` → if prior status was `PENDING/UPLOADED` also `MinIO.delete_object` → final tx: delete row → 204). `rowcount=0` → silent 204 (idempotent re-DELETE or row already DELETING).
2. Any mid-cascade failure → row stays `DELETING`; Reconciler resumes idempotently.

**BDD:**
- **S1** POST 1 MB `.txt` → 202 + 26-char task_id; status → `READY` within 60 s; chunks in ES.
- **S12** DELETE cascade on `READY` doc → `DELETING` → all plugins called once → ES/row cleared → 204 (MinIO already cleared at READY).
- **S13** Any failure mid-delete → row stays `DELETING`; Reconciler resumes ≤ 5 min.
- **S14** Re-DELETE an already-deleted document → 204, no plugin calls.
- **S15** `GET /ingest?limit=2` on 5 docs → ≤ 2 items + `next_cursor` continues.
- **S17 supersede happy path** — Given a `READY` doc D1 with `(source_id="X", source_app="confluence")`, When client POSTs another file with the same pair, Then a new doc D2 is created; while D2 is `PENDING`, queries still see D1 chunks; when D2 reaches `READY`, supersede task cascade-deletes D1; queries now see only D2 chunks.
- **S18 supersede on failure preserves old** — Given D1 `READY` with `(source_id, source_app)=("X","confluence")`, When new D2 with the same pair ends up `FAILED`, Then D1 remains `READY` and queryable; supersede task is **not** enqueued.
- **S20 supersede out-of-order finish** — Given D1 (created at t=0) and D2 (created at t=1) share `(source_id="X", source_app="confluence")`, When D2 reaches `READY` first (D1 still `PENDING`) and D1 reaches `READY` later, Then after both supersede tasks run only D2 (the row with `MAX(created_at)`) remains; D1 is cascade-deleted.
- **S22 same source_id different source_app coexist** — Given doc D1 with `(source_id="X", source_app="confluence")` is `READY`, When client POSTs with `(source_id="X", source_app="slack")`, Then both reach `READY` and coexist; supersede touches neither.
- **S23 missing source_id, source_app, or source_title** — Given a POST omits any of `source_id` / `source_app` / `source_title`, Then router returns 422 problem+json with field-level `errors[]`; no MinIO upload, no DB row.
- **S24 UPLOADED orphan recovered (R1)** — Given a row in `UPLOADED` for > 5 min (TaskIQ message lost or broker outage at POST time), When the Reconciler runs, Then it re-kiqs `ingest.pipeline` and the doc proceeds normally.
- **S26 multi-READY invariant repaired (R3)** — Given two `READY` rows for the same `(source_id, source_app)` (e.g. supersede task lost between status commit and kiq), When the Reconciler runs, Then it re-enqueues `ingest.supersede` and convergence happens within one cycle.
- **S27 FAILED transition cleans partial output (R5)** — Given a doc transitions to `FAILED`, When the FAILED state is committed, Then `chunks` and ES `chunks_v1` for that `document_id` have been cleared (no leakage into chat retrieval).
- **S41 manual rerun** — Given a document with status in `{UPLOADED, PENDING, FAILED}`, When `POST /ingest/v1/{id}/rerun` is called with `X-User-Id`, Then the row's `status` is flipped to `PENDING`, `attempt` is reset to `0` (so an exhausted FAILED row isn't immediately re-FAILED by the reconciler's `_mark_failed` budget check), `error_code`/`error_reason` are cleared, `ingest.pipeline` is re-enqueued, and the response is `202 {"document_id": ...}`. Given the status is `READY` or `DELETING`, the response is `409 INGEST_NOT_RERUNNABLE` (use re-POST with same `(source_id, source_app)` for READY supersede). Given the document does not exist, the response is `404 INGEST_NOT_FOUND`.

---

### 3.2 Indexing Pipeline

> **v2 Pipeline:** `_TextLoader → FileTypeRouter` branches by MIME: `text/plain → DocumentSplitter` · `text/markdown → _MarkdownASTSplitter` (mistletoe AST) · `text/html → _HtmlASTSplitter` (selectolax) · `docx → _DocxASTSplitter` · `pptx → _PptxASTSplitter` · unclassified → `_RaiseUnroutable` (PIPELINE_UNROUTABLE) → `DocumentJoiner → _IdempotencyClean → _BudgetChunker(1000/1500/100, mime-agnostic) → DocumentEmbedder(bge-m3, batch=32) → DocumentWriter(ES chunks_v1)`.
> Each splitter sets `meta["raw_content"]` = exact byte slice. `chunks_v1` stores `content` (BM25-analyzed) and `raw_content` (`index: false`); LLM context uses `raw_content`.

**Performance:** `EmbeddingClient` batched at 32 chunks/call. All external calls carry explicit timeouts (see §4.6.7); `PIPELINE_TIMEOUT_SECONDS` (default 1800 s) bounds the overall body. Pipeline runs with no open DB transaction (§3.1 locking discipline).

---

### 3.3 Pluggable Extractors

**Protocol v1 (frozen):**

```python
@runtime_checkable
class ExtractorPlugin(Protocol):
    name: str; required: bool; queue: str
    def extract(self, document_id: str) -> None: ...
    def delete(self, document_id: str) -> None: ...
    def health(self) -> bool: ...
```

**P1 plugins:** `VectorExtractor` (required, ES bulk), `StubGraphExtractor` (optional, no-op). See §4.4.

**Plugin construction (B17):** the Protocol freezes the **interface** (`extract`, `delete`, `health` plus three attributes) but plugins are **dependency-injected** via their constructor. `VectorExtractor.__init__(repo: DocumentRepository, chunks: ChunkRepository, embedder: EmbeddingClient, es: ElasticsearchClient)` — `extract(document_id)` reads `source_title` from `repo` and chunk rows from `chunks`. Plugins MUST NOT import `pipelines/` or HTTP layers; they accept their dependencies as constructor args, the registry simply holds the constructed instances.

**Registry:**
- `register()` raises `DuplicatePluginError` on name conflict.
- `fan_out(document_id)` → dispatch extract to all plugins concurrently; **per-plugin timeout 60 s** (overrun → `Result(error="timeout")`); `all_required_ok(results)` gates `READY`.
- `fan_out_delete(document_id)` → dispatch delete to all plugins concurrently; **per-plugin timeout 60 s**; runs **outside any DB transaction** (no row locks held during plugin network calls — P-E).

**BDD:**
- **S4 Protocol conformance** — Given an object missing any of `name` / `required` / `queue` / `extract` / `delete` / `health`, When `isinstance(obj, ExtractorPlugin)` is evaluated, Then it returns `False` (and `register()` raises before fan_out).
- **S5 stub no-op extract → READY** — Given a registered `StubGraphExtractor` (optional, no-op `extract`), When the worker runs `fan_out(document_id)`, Then `Result(ok=True)` is returned in 0 ms and `all_required_ok` does not depend on it (since `required=False`).
- **S11 duplicate registration** — Given a `PluginRegistry` already holding a plugin named `vector`, When `register()` is called with another plugin of the same `name`, Then it raises `DuplicatePluginError` and the existing instance is unaffected.

---

### 3.4 Chat Pipeline

`QueryEmbedder → {ESVectorRetriever(kNN) ∥ ESBM25Retriever(multi_match) ∥ FeedbackMemoryRetriever(kNN, when CHAT_FEEDBACK_ENABLED+rrf, B54)} → DocumentJoiner(RRF, weights=[1,1,CHAT_FEEDBACK_RRF_WEIGHT]) → SourceHydrator(JOIN documents) → LLMClient.{chat|stream}`

Title participates in **both** retrieval surfaces (B15): semantic (baked into every chunk's `embedding` at ingest via `embed(f"{title}\n\n{text}")`) and lexical (BM25 boosted 2× via `multi_match`). No separate title-only retriever, no extra ES field beyond `title`.

**SourceHydrator gate (B36):** Chunks whose `document_id` has no `READY` row in `documents` are **dropped** (not passed through with empty fields). Orphan chunks, in-flight rows, `DELETING` rows never reach LLM or `sources[]`. Reconciler = disk reclaim, not retrieval correctness.

**Filter scope (B29 → B35):** Optional `source_app` / `source_meta` request params (§3.4.1) translate to ES `term` filters applied to both retrievers' `filter` clause. Both fields denormalised onto chunks at ingest (§5.2). Empty filter ⇒ unrestricted retrieval (current P1 behaviour). Both filters AND together when both are supplied.

**Two endpoints (B12):** `POST /chat` (sync JSON) and `POST /chat/stream` (SSE: `delta` events → terminal `done` event carrying the same §3.4.2 body). Same request schema; only the LLM call differs.

**Join mode (`CHAT_JOIN_MODE`):** `rrf` (default, RRF k=60) | `concatenate` | `vector_only` | `bm25_only`. Factory assembles graph at startup; chat router is mode-agnostic.

**P1 OPEN:** no permission gating. ES queries permission-blind in every phase; Permission Layer post-filters in P2+ (§3.5). P1 retrievers run sequentially; P2 makes them concurrent (AsyncPipeline).

#### 3.4.1 Request schema (shared by `/chat` and `/chat/stream`)

```json
{
  "messages":         [{"role": "system|user|assistant", "content": "..."}],
  "provider":         "openai",
  "model":            "gptoss-120b",
  "temperature":      0.7,
  "maxTokens":        4096,
  "source_app":       "confluence",
  "source_meta":      "engineering",
  "top_k":            20,
  "min_score":        null
}
```

- `messages` required; all other fields optional (fall back to defaults above).
- `source_app` (≤ 64) / `source_meta` (≤ 1024): optional ES `term` filters (AND when both); empty string → 422 `CHAT_FILTER_INVALID`; omit to skip (B29→B35).
- `top_k` (default `RETRIEVAL_TOP_K`, default 20, range 1–200): max chunks to LLM context.
- `min_score` (default `RETRIEVAL_MIN_SCORE`, default `null`): score floor; `null` = no filtering.
- `maxTokens` caps LLM output; `provider` validated against `{"openai"}` allow-list (B22), echoed verbatim, 422 `CHAT_PROVIDER_UNSUPPORTED` otherwise.
- Missing `role:"system"` → server prepends default. Retrieval query = last `role:"user"` message.

#### 3.4.2 Response schema

`/chat` (non-streaming, `Content-Type: application/json`) and the terminal `done` event of `/chat/stream` both carry:

```json
{
  "content":        "COMPLETE_MARKDOWN_RESPONSE",
  "usage":          {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0},
  "model":          "gptoss-120b",
  "provider":       "openai",
  "sources":        [
    {
      "document_id":  "01J9...",
      "source_app":   "confluence",
      "source_id":    "DOC-123",
      "source_meta":  "engineering",
      "type":         "knowledge",
      "source_title": "Q3 OKR Planning",
      "source_url":   "https://wiki.example/q3-okr",
      "mime_type":    "text/markdown",
      "excerpt":      "...chunk text snippet...",
      "score":        0.87
    }
  ],
  "request_id":     "01J9...",
  "feedback_token": "<base64url>.<hmac_hex>"
}
```

- `sources` is `null` when empty; otherwise all fields are populated. `type` is always `"knowledge"` in P1 (reserved enum).
- `sources[].source_title/url/mime_type` from `documents`; `score` is RRF retrieval score; `excerpt` is truncated to `EXCERPT_MAX_CHARS` (default 512) in router (B23) — LLM receives full text untruncated.
- `usage` from `LLMClient` (non-streaming only; streaming `done` event omits `usage` — P1 limitation).
- `request_id` + `feedback_token` are present **only when `CHAT_FEEDBACK_ENABLED=true` AND the request carried `X-User-Id`** (B55, T-FB.10). Both omitted otherwise — clients should treat them as optional and skip the `/feedback/v1` UI when absent.

#### 3.4.3 Streaming wire format (`/chat/stream` only)

```
data: {"type":"delta","content":"<token chunk>"}\n\n
…
data: {"type":"done","content":"<full>","model":"…","provider":"…","sources":[…]}\n\n
```

**Error mid-stream (B6):** If the LLM or any retriever fails *after* the first `delta` has been written, the server emits a single default-event `data:` line with payload `{"type":"error","error_code":"<CODE>","message":"<text>"}` and closes the connection. **No `event: error` named-event is used.** Pre-stream failures (before the first `delta`) return a normal RFC 9457 problem+json response. `/chat` always uses problem+json on error (it has no streaming surface).

**BDD:**
- **S6**  — `POST /chat/stream` emits ≥ 1 `data: {type:"delta",...}` then exactly one `data: {type:"done",...}` carrying `content`, `usage`, `model`, `provider`, `sources`.
- **S6a** — `POST /chat` returns `200 application/json` with the full §3.4.2 body (single response, no streaming framing).
- **S6e** — Every emitted `sources[]` entry has all six fields populated and `type="knowledge"`.
- **S6j orphan chunk dropped (B36)** — ES chunk with no `READY` `documents` row is dropped by `_SourceHydrator`; never reaches `sources[]` or LLM context (applies to `/chat`, `/chat/stream`, `/retrieve`).
- **S6f filter by source_app (B29)** — Both retrievers apply `term: {source_app}` filter; `sources[]` contains only matching rows.
- **S6g filter AND (B29→B35)** — Both `source_app` and `source_meta` supplied → AND filter; only rows matching both appear.

---

#### 3.4.4 `POST /retrieve` — Retrieval without LLM

Runs the full retrieval pipeline (embed → kNN + BM25 → RRF join → source hydration) and returns ranked chunks without invoking the LLM. Useful for retrieval quality inspection and custom UIs.

**Request schema:**

```json
{
  "query":            "What are our Q3 OKRs?",
  "source_app":       "confluence",
  "source_meta":      "engineering",
  "top_k":            20,
  "min_score":        0.3,
  "dedupe":           false
}
```

- Only `query` is required.
- `source_app` / `source_meta`: same optional ES `term` filters as `/chat` (B29 → B35).
- `top_k` (default `RETRIEVAL_TOP_K`, env-configurable, default 20): max chunks returned from the retrieval pipeline; range 1–200.
- `min_score` (default `null`): post-retrieval score threshold — chunks whose retrieval score is below this value are dropped. Applied after `pipeline.run()` (not passed to ES retrievers, which do not accept a score threshold param).
- `dedupe` (default `false`): when `true`, keeps only the highest-scored chunk per `document_id` (pipeline output is already score-sorted, so first-seen = best).

**Response schema:**

```json
{
  "chunks": [
    {
      "document_id":  "01J9...",
      "source_app":   "confluence",
      "source_id":    "DOC-123",
      "source_meta":  "engineering",
      "type":         "knowledge",
      "source_title": "Q3 OKR Planning",
      "source_url":   "https://wiki.example/q3-okr",
      "mime_type":    "text/markdown",
      "excerpt":      "...truncated to EXCERPT_MAX_CHARS...",
      "score":        0.87
    }
  ]
}
```

- `chunks` is an empty array when no results are found (never `null`).
- `excerpt` is truncated to `EXCERPT_MAX_CHARS` (default 512) in the router — same rule as `/chat` `sources[].excerpt` (B23).
- `dedupe=false`: the same `document_id` can appear multiple times if multiple chunks from the same document ranked highly.
- `dedupe=true`: one entry per `document_id`; the chunk with the highest RRF score is kept.

**BDD:**
- **S38 retrieve returns all chunks by default** — Given two chunks from the same `document_id` both rank in the top-K, When `POST /retrieve {"query":"..."}` (no `dedupe`), Then both appear in `chunks[]` with the same `document_id`.
- **S39 retrieve dedupe=true keeps best chunk** — Given the same two chunks, When `POST /retrieve {"query":"...","dedupe":true}`, Then exactly one entry with that `document_id` appears, and its `excerpt` matches the higher-scored chunk.
- **S41 retrieve filter (B29 → B35)** — Same `source_app` / `source_meta` filter semantics as `/chat`; non-matching filter returns `{"chunks":[]}`.

- **S37 chat rate-limit per user (B31)** — Given `CHAT_RATE_LIMIT_PER_MINUTE=N` and a single `X-User-Id`, When the same caller issues N+1 `POST /chat` (or `/chat/stream`) requests within the window, Then the first N succeed and the (N+1)th returns 429 `application/problem+json` with `error_code=CHAT_RATE_LIMITED` and a `Retry-After` header equal to seconds until window reset. A different `X-User-Id` gets an independent budget; ingest, MCP, and health endpoints are unaffected. After the window expires the counter resets.

---

#### 3.4.5 `POST /feedback/v1` — User feedback on chat sources (B54/B55/B56, T-FB.6)

Closes the feedback loop: client echoes back the HMAC-signed token from a prior `/chat` response and reports a vote against one of the shown sources. Dual-writes MariaDB `feedback` (§5.1) and ES `feedback_v1` (§5.4); next `/chat` consults the ES index via `_FeedbackMemoryRetriever` (B54) when `CHAT_FEEDBACK_ENABLED=true`.

**Request schema:**

```json
{
  "request_id":     "01J9...",
  "feedback_token": "<base64url>.<hmac_hex>",
  "query_text":     "what are our Q3 OKRs?",
  "shown_sources":  [
    {"source_app": "confluence", "source_id": "DOC-A"},
    {"source_app": "confluence", "source_id": "DOC-B"},
    {"source_app": "drive",      "source_id": "DOC-C"}
  ],
  "source_app":     "confluence",
  "source_id":      "DOC-A",
  "vote":           1,
  "reason":         "irrelevant",
  "position_shown": 0
}
```

- `request_id`, `feedback_token`: from the `/chat` response (§3.4.2). Token TTL = 7 days. The token's signed `request_id` and `user_id` are **authoritative**; the body's `request_id` must equal the signed value, and (if `X-User-Id` is present) it must equal the token's `user_id`. Mismatch → 401 `FEEDBACK_TOKEN_INVALID`.
- `query_text`, `shown_sources`: re-supplied by client; HMAC over `sources_hash = sha256(json([[source_app, source_id], …]))` binds the **pair** list (document identity is `(source_app, source_id)` per B11/B35).
- `(source_app, source_id)` of the voted source ∈ `shown_sources` (server-enforced).
- `vote` ∈ {+1, -1}.
- `reason` ∈ {`irrelevant`, `hallucinated`, `outdated`, `incomplete`, `wrong_citation`, `other`} (B56, frozen) or omitted.
- `position_shown`: 0-based index of the voted source in the original `/chat sources[]` (recorded for future IPS; B57 item 1, unused in P1).

**Response:** `204 No Content` on success; `application/problem+json` on error per §4.1.1.

**Errors:**
- `401 FEEDBACK_TOKEN_INVALID` — HMAC mismatch, malformed token, `request_id` mismatch with signed value, `X-User-Id` mismatch with signed `user_id`, or `shown_sources` doesn't match the signed `sources_hash`.
- `410 FEEDBACK_TOKEN_EXPIRED` — token `ts` outside the 7-day window.
- `422 FEEDBACK_SOURCE_INVALID` — voted `(source_app, source_id)` pair not in `shown_sources`.
- `422 FEEDBACK_VALIDATION` — schema violations (vote ∉ {±1}, reason outside enum, missing required field).

**Dual-write semantics (B55):**
1. MariaDB `feedback` UPSERT keyed on `(user_id, request_id, source_app, source_id)` — idempotent; second vote overwrites.
2. ES `feedback_v1` index with `_id = sha256(user_id|request_id|source_app|source_id)` — same idempotency. Re-embeds `query_text` once per call via `EmbeddingClient.embed([query_text], query=True)`. ES doc carries `source_app` (`_FeedbackMemoryRetriever` aggregates by the pair).
3. ES leg failure logs `event=feedback.es_write_failed` and increments `ragent_feedback_es_write_failed_total`; the request still returns 204 because MariaDB is the truth.

**BDD:**
- **S42 happy** — Valid token + valid shape → 204; MariaDB row exists; ES doc indexed with `source_app` field present.
- **S43 tampered token** — Single byte flip → 401 `FEEDBACK_TOKEN_INVALID`.
- **S44 expired token** — `ts` > 7 days ago → 410 `FEEDBACK_TOKEN_EXPIRED`.
- **S45 sources_hash mismatch** — Client supplies different `shown_sources` than at sign time → 401 `FEEDBACK_TOKEN_INVALID`.
- **S46 source not shown** — Voted `(source_app, source_id)` pair not in `shown_sources` → 422 `FEEDBACK_SOURCE_INVALID`.
- **S47 reason enum** — Reason outside B56 frozen set → 422.

---

### 3.5 Authentication & Permission

Two distinct concerns, kept architecturally separate from retrieval:

| Concern | Question answered | Mechanism | P1 | Future phase |
|---|---|---|---|---|
| **Authentication** | Who is the caller? | JWT verify (`exp` expiry check) → `user_id = preferred_username` claim | OFF — `X-User-Id` header trusted, validated non-empty | JWT validated via FastAPI dependency; `RAGENT_TRUST_X_USER_ID_HEADER=true` falls back to header (dev/integration override) |
| **Permission** | Can this caller see this document? | Permission Layer service that calls **OpenFGA** | OPEN — no checks, all docs visible | `PermissionClient.batch_check(user_id, document_ids)` returns the allowed subset; gated per-surface by `RAGENT_PERMISSION_INGEST_ENABLED` / `RAGENT_PERMISSION_CHAT_ENABLED` (both default `false` even in P2) |

**Design principle:** ES (`chunks_v1`) carries **no auth fields** in any phase — retrieval is permission-blind. The Permission Layer post-filters by `document_id`, keeping ES schema stable across phases.

**P1 (current phase):** No JWT — `X-User-Id` trusted, written to `documents.create_user` (audit only, not authz). No permission gating — all chunks visible. `auth_mode=open` in audit logs. **TokenManager (J1→J2) is active** for Embedding/LLM/Rerank API auth (unrelated to user auth).

**P2 additions:** JWT (`exp`/`preferred_username` claims; 401 on absent/expired/invalid); `RAGENT_TRUST_X_USER_ID_HEADER=true` (non-prod only) bypasses JWT. `PermissionClient(OpenFGA)` post-filters retrieved chunks; gated by `RAGENT_PERMISSION_INGEST_ENABLED` / `RAGENT_PERMISSION_CHAT_ENABLED` (both default `false`). ES queries remain permission-blind in every phase.

**BDD:**
- **S9 token refresh at boundary** — Given `TokenManager` cache holds a J2 with `expiresAt = T0 + 60 min`, When the wall clock advances to `T0 + 55 min` (`expiresAt − 5 min`) and a caller asks for the J2 token, Then `TokenManager` issues exactly one J1→J2 refresh HTTP exchange and returns the new token; 100 concurrent callers around the boundary share that single refresh (single-flight, P-F).
- Permission-gating BDD specified when the P2 plan is written.

---

### 3.6 Resilience

**Reconciler (Kubernetes `CronJob`, schedule `*/5 * * * *`, `SELECT … FOR UPDATE SKIP LOCKED`) — B9:**

> Implementation = a one-shot Python entrypoint (`python -m ragent.reconciler`) packaged in the same image, scheduled by **K8s CronJob** with `concurrencyPolicy: Forbid` and `successfulJobsHistoryLimit: 3`. Not a TaskIQ scheduled task (decouples sweeper liveness from broker health — Reconciler is the recovery surface for broker outage itself, see R1).

- `UPLOADED, updated_at < NOW() - 5 min` → re-kiq `ingest.pipeline` (R1 — covers TaskIQ message loss and broker outage at POST time).
- `PENDING, updated_at < NOW() - 5 min, attempt ≤ 5` → **stale heartbeat (B16)** ⇒ worker is dead or hung ⇒ re-kiq `ingest.pipeline` (idempotent key: `document_id + attempt`). A live worker keeps its row's `updated_at` fresh and is never re-dispatched.
- `PENDING, updated_at < NOW() - 5 min, attempt > 5` → `FAILED` (cleans chunks/ES per §3.1 R5 path) + structured-log `event=ingest.failed`.
- `DELETING > 5 min` → resume cascade delete idempotently.
- **Multi-READY invariant repair (R3):** every cycle also runs `SELECT source_id, source_app FROM documents WHERE status='READY' GROUP BY source_id, source_app HAVING COUNT(*) > 1` and re-enqueues `ingest.supersede` for each pair.
- **Heartbeat (R8):** every tick increments `reconciler_tick_total` and emits `event=reconciler.tick`. Prometheus alert fires if no tick observed for > 10 min (Reconciler is itself a single point of failure).

**BDD:**
- **S2** Given a `PENDING` document older than 5 min with `attempt ≤ 5`, When the reconciler runs, Then it re-kiqs `ingest.pipeline` exactly once per cycle (idempotent across redelivery).
- **S3** Given a `PENDING` document with `attempt > 5`, When the reconciler runs, Then status transitions to `FAILED`, partial output is cleaned, and a structured log line `event=ingest.failed` is emitted.
- See also S24 (UPLOADED orphan), S26 (multi-READY repair), S30 (heartbeat).

**Infrastructure (B27):** Redis broker (TaskIQ) and Redis rate-limiter are **separate logical instances**, each independently configurable as **standalone or Sentinel** via `REDIS_MODE` env (default `standalone` for dev/CI, set `sentinel` in prod). Sentinel mode shares a single sentinel quorum (`REDIS_SENTINEL_HOSTS`, ≥ 3 nodes) and resolves each instance by its master name (`REDIS_BROKER_SENTINEL_MASTER`, `REDIS_RATELIMIT_SENTINEL_MASTER`). Standalone mode reads direct URLs (`REDIS_BROKER_URL`, `REDIS_RATELIMIT_URL`). Connection layer uses `redis-py-sentinel` when mode=sentinel, plain `redis-py` when mode=standalone. The same code path is used by both the API process and the worker.

#### 3.6.1 Chaos drill suite (P2.6 軌三 / T7.4.x)

The chaos suite asserts the resilience claims of §3.6 (reconciler recovery, idempotent retries, partial-failure tolerance) hold under realistic injected faults. Each case is its own e2e file under `tests/e2e/test_chaos/test_C<N>_<scenario>.py`, marked `@pytest.mark.docker`, gated by a nightly CI lane (not per-PR; injection drills are slow). Case matrix (B49):

| # | Case / Injection | Expected terminal state |
|---|---|---|
| **C1** | Worker SIGKILL after PENDING (`os.kill` after status flip) | Reconciler re-dispatch → READY; `reconciler_tick_total` increments; no orphan chunks |
| **C2** | MariaDB commit ↔ ES bulk split (monkeypatch `ConnectionError`) | Worker retries idempotently → READY; chunks present; `multi_ready_repaired_total` unchanged |
| **C3** | ES bulk 207 partial failure (WireMock, 5/50 items failed) | Worker retries failed items; READY with all 50 chunks; `event=es.bulk_partial_failure` |
| **C4** | Rerank 5xx (WireMock 3×500 on `/rerank`) | Chat 200 with RRF-ordered sources (fail-open); `rerank_degraded_total{reason="5xx"}` increments |
| **C5** | LLM stream TCP drop mid-response (WireMock drops after 3 deltas) | Server emits `{type:"error", error_code:"LLM_STREAM_INTERRUPTED"}` per B6; no 500 in logs |
| **C6** | MinIO 503 2/3 attempts (WireMock proxy) | Worker retries (3×@2s); READY on attempt 3; `minio.transient_error` log count = 2 |

**Common asserts**: terminal status; ES/DB consistency (no orphans); per-case OTEL spans; `chaos_drill_outcome_total{case,outcome}` increments.

---

### 3.7 Observability

- Haystack auto-trace + FastAPI OTEL middleware → Tempo + Prometheus.
- Structured logs for state-machine transitions; `auth_mode=open` field in P1.
- **Heartbeat metrics (R8):** `reconciler_tick_total` (counter); Prometheus alert when missing > 10 min. Worker emits `worker_pipeline_duration_seconds` (histogram) and `event=ingest.{started,failed,ready}`.
- **Orphan/leak counters:** `minio_orphan_object_total` (post-commit cleanup failure), `multi_ready_repaired_total` (Reconciler R3 sweep).
- **ES events (B26):** `event=es.bbq_unsupported` (cluster rejected `bbq_hnsw`; bootstrap retried with standard HNSW); `event=schema.drift` (resource file ↔ live mapping mismatch). Both surface in `/readyz` as degraded (B4).
- **Structured logging (structlog).** JSON to stdout. Categories: (1) **API trace** (`api.request/error`) — per-request `{request_id, method, path, status_code, duration_ms, user_id, trace_id}` via `RequestLoggingMiddleware` (excl. /livez, /readyz, /metrics). (2) **Business** — `chat.retrieval/llm`, `ingest.failed/ready`, `reconciler.tick`, `embedding/rerank.call`, etc., paired with OTEL spans sharing `trace_id`. (2a) **Per-step pipeline** (T2v.42/T-APL.7) — `ingest.step.{started,ok,failed}` and `chat.step.{started,ok,failed}` carry `{step, duration_ms, atoms_in?, chunks_out?, error_code?}` plus inherited contextvars (`request_id`/`user_id` from middleware, `document_id`/`mime_type` from the ingest worker). Emitted by every Haystack component wrapped via `wrap_pipeline_component(*, namespace, step)`. **Dual emission (T-APL.11):** the same wrapper also opens an OTEL span named `{namespace}.step.{step}` with attributes `{pipeline.namespace, pipeline.step, atoms_in?, chunks_out?, duration_ms}` so traced deployments get waterfall visibility without re-instrumentation; un-traced processes (no `setup_tracing()`) hit the NoOp tracer and pay only context-manager enter/exit cost. **Cross-process correlation (T-APL.9):** `request_id` and `user_id` propagate from the api process across the TaskIQ enqueue/execute seam via `StructlogContextMiddleware` (snapshot to `message.labels` on `pre_send`, rebind on `pre_execute`) so worker-side `ingest.*` logs carry the originating HTTP request's id. (3) **Error** — `error_type, error_code`, traceback, redacted. Format: ISO 8601 UTC; `LOG_FORMAT=console` for dev. **Privacy:** identity + metric fields only; denylist processor drops `query/prompt/messages/completion/chunks/embedding/documents/body/authorization/cookie/password/token/secret` and stamps `content_redacted=true`. `HAYSTACK_CONTENT_TRACING_ENABLED` pinned off.

---

### 3.8 MCP Tool Server (P2.5)

Exposes ragent's retrieval pipeline as a **Model Context Protocol** tool so external LLM agents (Claude Desktop, Cursor, in-house agents) can call ragent's corpus through the MCP standard rather than a bespoke HTTP shape. The MCP server **wraps `POST /retrieve/v1`** (§3.4.4) — it does NOT call the LLM. The calling agent's own LLM does the synthesis; ragent supplies the grounded chunks.

**Decision (B47):** P2.5 implements a **real MCP server speaking JSON-RPC 2.0** (not the P1 stub's REST shape). The P1 `POST /mcp/v1/tools/rag` 501 endpoint is **removed** and replaced by `POST /mcp/v1` carrying JSON-RPC envelopes. This is the user-requested Option B (full MCP, retrieve-only). Option A (REST tool-call) and Option C (REST + thin MCP shim) were rejected because they either misrepresent the protocol (A) or carry two surfaces with the same behavior (C).

#### 3.8.1 Protocol

- **Transport:** Streamable HTTP, request/response subset (POST only; no server-initiated SSE in P2.5). Pinned MCP spec revision: `"2024-11-05"`.
- **Endpoint:** `POST /mcp/v1` (single endpoint; method dispatched from JSON-RPC `method` field).
- **Envelope:** Standard JSON-RPC 2.0 request/success-result/error envelopes. `id` is `null` when parse fails.
- **Notification** (no response): omit `id`. P2.5 supports `notifications/initialized` only.
- **Auth:** `Authorization: Bearer <jwt>` (P2.2 onwards) or `X-User-Id` fallback (`RAGENT_TRUST_X_USER_ID_HEADER=true`, dev only). Auth applies before JSON-RPC dispatch; failure returns HTTP 401 with `application/problem+json` (NOT a JSON-RPC error — auth is a transport-layer concern).
- **Stateless mode:** P2.5 only (no `Mcp-Session-Id`). Body cap: `MCP_REQUEST_MAX_BYTES` (default 256 KiB) → HTTP 413. Array batch requests → `-32600 Invalid Request`.

#### 3.8.2 Supported methods

| Method | Direction | Purpose |
|---|---|---|
| `initialize` | client → server | Capability negotiation. Returns `{protocolVersion, capabilities, serverInfo}`. |
| `notifications/initialized` | client → server (notification) | Client signals init complete. Server silently accepts. |
| `tools/list` | client → server | Returns `{tools: [{name, description, inputSchema}]}`. |
| `tools/call` | client → server | Invokes a tool. Returns `{content: [{type, text}], isError}`. |
| `ping` | bidirectional | Returns `{}`. Optional keepalive. |

Any other method → JSON-RPC error `-32601 Method not found`.

#### 3.8.3 The `retrieve` tool

The sole tool advertised by `tools/list`. Mirrors §3.4.4 `POST /retrieve/v1` semantics:

Tool: `name: "retrieve"` — hybrid vector+BM25 search returning ranked chunks (no LLM). Input schema mirrors `POST /retrieve/v1` (§3.4.4): `query` (string, required), `top_k` (int 1–200, default 20), `source_app` (string ≤64, optional filter), `source_meta` (string ≤1024, optional filter), `min_score` (number ≥0), `dedupe` (bool, default false).

`tools/call` result: `{content: [{type: "text", text: "<JSON-stringified RetrieveResponse>"}], isError: false}`. Empty-results retrieval is also `isError: false` with empty `chunks[]`. **Retrieval pipeline crashes return a JSON-RPC `error` envelope (`code: -32001`, NOT `isError: true`)** — only soft tool errors (e.g. future capability flags) would use `isError: true`.

#### 3.8.4 Error codes (JSON-RPC layer)

Standard JSON-RPC codes: `-32700` (parse), `-32600` (invalid request), `-32601` (method not found), `-32602` (invalid params), `-32603` (internal). App-level `-32001` = tool execution failed (`MCP_TOOL_EXECUTION_FAILED`). All app errors carry `data.error_code` matching §4.1.2 catalog.

#### 3.8.5 BDD

- **S58 mcp initialize** — `initialize` with `protocolVersion:"2024-11-05"` → `result.{protocolVersion:"2024-11-05", capabilities:{tools:{}}, serverInfo:{name:"ragent",version:"<semver>"}}`.
- **S59 mcp tools/list** — `result.tools` has exactly one entry `name:"retrieve"` with `inputSchema` matching §3.8.3.
- **S62 mcp tools/call invalid name** — Given `{method:"tools/call", params:{name:"unknown_tool",arguments:{}}}`, Then `error.code` is `-32602` and `error.data.error_code` is `MCP_TOOL_NOT_FOUND`.
- **S63 mcp tools/call missing query** — Given `{method:"tools/call", params:{name:"retrieve",arguments:{}}}` (no `query`), Then `error.code` is `-32602` and `error.data.error_code` is `MCP_TOOL_INPUT_INVALID`.
- **S66 mcp auth required** — Given `RAGENT_AUTH_DISABLED=false` and no `Authorization` header, Then HTTP `401` with `application/problem+json` (NOT a JSON-RPC error envelope) and `error_code=AUTH_CLAIM_MISSING`.

---

## 4. Inventories

### 4.1 Endpoints

> **v2 OVERRIDE for `POST /ingest`** — JSON body only (no multipart); request schema in §3.1. Validation order: discriminator-shape (422) → `mime_type ∈ {text/plain,text/markdown,text/html}` (415) → inline `len(content.encode("utf-8")) ≤ INGEST_INLINE_MAX_BYTES` / file HEAD-probe size ≤ `INGEST_FILE_MAX_BYTES` (413) → `minio_site` resolved against `MinioSiteRegistry` (422 `INGEST_MINIO_SITE_UNKNOWN`) → file HEAD-probe object exists (422 `INGEST_OBJECT_NOT_FOUND`). Worker-side guards run before splitter parse: DOCX/PPTX zip preflight (`INGEST_MAX_ARCHIVE_MEMBERS` / `_RATIO` / `_EXPANDED_BYTES`) → 413 `INGEST_ARCHIVE_UNSAFE` persisted as `documents.error_code` with terminal `FAILED`; PDF page-count cap (`INGEST_MAX_PDF_PAGES`) → 413 `INGEST_PDF_TOO_MANY_PAGES` likewise. Every guard rejection increments `ragent_ingest_rejected_total{reason}` (T-SEC.7).

| Method | Path | P1 Auth | Request | Response |
|---|---|---|---|---|
| POST   | `/ingest/v1`               | `X-User-Id` | **JSON** (v2, see override above) | `202 { task_id }` — `task_id` **is** the `document_id`. |
| GET    | `/ingest/v1/{id}`          | `X-User-Id` | — | `200 { status, attempt, updated_at }` |
| GET    | `/ingest/v1?after=&limit=&source_id=&source_app=` | `X-User-Id` | — | `200 { items, next_cursor }` (limit ≤ 100; ordered `document_id DESC`; `source_id`/`source_app` are optional exact-match filters) |
| DELETE | `/ingest/v1/{id}`          | `X-User-Id` | — | `204` idempotent |
| POST   | `/ingest/v1/{id}/rerun`    | `X-User-Id` | — | `202 { document_id }` — manual re-dispatch of `ingest.pipeline` for non-READY/non-DELETING rows; `404 INGEST_NOT_FOUND` / `409 INGEST_NOT_RERUNNABLE` per S41. |
| POST   | `/ingest/v1/upload`        | `X-User-Id` | `multipart/form-data` (server stages to `__default__` MinIO; identical downstream to inline) | `202 { document_id }` |
| POST   | `/retrieve/v1`             | `X-User-Id` | §3.4.4 schema (`query` required; rest default) | `200 { chunks[] }` per §3.4.4 |
| POST   | `/chat/v1`                 | `X-User-Id` | §3.4.1 schema (`messages` required; rest default) | `200 application/json` per §3.4.2 |
| POST   | `/chat/v1/stream`          | `X-User-Id` | §3.4.1 schema | `text/event-stream` per §3.4.3 (`data: {type:delta\|done\|error}`) |
| POST   | `/feedback/v1`             | `X-User-Id` | §3.4.5 schema | `204` on success; `401`/`410`/`422` `application/problem+json` per §3.4.5. |
| POST   | `/mcp/v1`               | `X-User-Id` (P1) / `Authorization: Bearer` (P2) | JSON-RPC 2.0 envelope per §3.8 | `200` with JSON-RPC response envelope; `204` for `notifications/*`. Auth failure (401) returns `application/problem+json` per §3.8.1 (transport-layer). |
| GET    | `/livez`                | none        | — | `200 {"status":"ok"}` — process up; no dependency probes |
| GET    | `/readyz`               | none        | — | `200` if all dep probes pass; else `503 application/problem+json` listing failed deps. Probes: **MariaDB** (`SELECT 1`), **ES** (`GET /_cluster/health` + `analysis-icu` plugin loaded + every `resources/es/*.json` index exists; B26, I5), **Redis broker & rate-limiter** (`PING` against active topology per `REDIS_MODE`; B27), **MinIO** (`ListBuckets`). Each probe ≤ 2 s. |
| GET    | `/metrics`              | none        | — | `200 text/plain; version=0.0.4` — Prometheus exposition (counters/histograms in §3.7) |

Future-phase auth: JWT verify (auth) + `PermissionClient` post-retrieval gate (permission, OpenFGA-backed) — see §3.5. ES queries remain permission-blind in every phase.

### 4.1.1 Error Response Schema (B5)

All non-2xx responses use **RFC 9457 Problem Details** (`Content-Type: application/problem+json`), extended with a business-semantic `error_code`:

```json
{
  "type":        "https://ragent.dev/errors/ingest-mime-unsupported",
  "title":       "Unsupported media type",
  "status":      415,
  "detail":      "MIME 'image/png' is not in the P1 allow-list",
  "instance":    "/ingest",
  "error_code":  "INGEST_MIME_UNSUPPORTED",
  "trace_id":    "01J9..."
}
```

- `error_code` is a stable `SCREAMING_SNAKE_CASE` string clients may switch on; HTTP status is for transport semantics only.
- `trace_id` echoes the OTEL trace id when present.
- 422 responses additionally include `errors: [{field, message}, …]` for field-level validation (e.g. missing `source_id`).
- **`/livez`, `/readyz`, `/metrics` are the only endpoints whose 2xx body is NOT problem+json**; their non-2xx still uses problem+json.

### 4.1.2 Error Code Catalog (I6)

Inventory of every `error_code` emitted by P1 (API responses + log events). New codes MUST be added here in the same commit that introduces them.

| `error_code` | HTTP / Surface | When | Origin |
|---|---|---|---|
| `INGEST_MIME_UNSUPPORTED`            | 415         | MIME outside the §4.2 P1 allow-list | Router T2.13 |
| `INGEST_FILE_TOO_LARGE`              | 413         | Multipart body > 50 MB | Router T2.13 |
| `INGEST_ARCHIVE_UNSAFE`              | 413 via `documents.error_code` | DOCX/PPTX zip preflight rejected the archive — `reason ∈ {invalid, members, ratio, expanded, per_member, traversal}` (T-SEC.3/.4) | Splitter T-SEC.4 |
| `INGEST_PDF_TOO_MANY_PAGES`          | 413 via `documents.error_code` | PDF page count exceeds `INGEST_MAX_PDF_PAGES` (T-SEC.5/.6) | Splitter T-SEC.6 |
| `INGEST_VALIDATION`                  | 422         | Missing/empty `source_id` / `source_app` / `source_title` (S23) — `errors[]` lists offending fields | Router T2.13 |
| `INGEST_MINIO_SITE_UNKNOWN`          | 422         | `minio_site` not in `MinioSiteRegistry` | Router T2.13 |
| `INGEST_OBJECT_NOT_FOUND`            | 422         | `(minio_site, object_key)` HEAD-probe miss | Router T2.13 |
| `INGEST_NOT_FOUND`                   | 404         | `GET /ingest/v1/{id}` / `DELETE /ingest/v1/{id}` / `POST /ingest/v1/{id}/rerun` on unknown id | Service T2.10 |
| `INGEST_NOT_RERUNNABLE`              | 409         | `POST /ingest/v1/{id}/rerun` on a document whose status is `READY` or `DELETING` (re-POST is the supersede path for READY; DELETING is mid-cascade) | Router (rerun endpoint) |
| `CHAT_MESSAGES_MISSING`              | 422         | `messages` absent or empty | Schema T3.3 |
| `CHAT_PROVIDER_UNSUPPORTED`          | 422         | `provider` outside `{"openai"}` allow-list (B22) | Schema T3.3 |
| `CHAT_FILTER_INVALID`                | 422         | `source_app` empty / > 64 chars, or `source_meta` empty / > 1024 chars (B29 → B35) | Schema T3.3 |
| `CHAT_RATE_LIMITED`                  | 429 + `Retry-After` | Per-user fixed-window quota exceeded on `/chat/v1` or `/chat/v1/stream` (B31, S37) | Router-level Depends T3.16 |
| `FEEDBACK_TOKEN_INVALID`             | 401         | HMAC mismatch, malformed token, or `shown_source_ids` doesn't match the signed `sources_hash` (T-FB.6, B55) | Router (feedback) |
| `FEEDBACK_TOKEN_EXPIRED`             | 410         | Token `ts` outside the 7-day window (T-FB.6, B55) | Router (feedback) |
| `FEEDBACK_SOURCE_INVALID`            | 422         | `source_id ∉ shown_source_ids` (T-FB.6) | Router (feedback) |
| `FEEDBACK_VALIDATION`                | 422         | Schema violations: vote ∉ {±1}, reason outside B56 enum, missing required field | Schema (feedback) |
| `CHAT_LLM_ERROR`                     | 502 / SSE-error | Pre-stream LLM failure (problem+json) or mid-stream LLM failure (`data: {type:error}`, B6) | Router T3.10/T3.12 |
| `CHAT_RETRIEVER_ERROR`               | 502 / SSE-error | ES vector / BM25 retriever failure | Router T3.10/T3.12 |
| `MCP_PARSE_ERROR`                    | JSON-RPC `-32700` | Request body is not valid JSON (S64) | Router P2.5 |
| `MCP_INVALID_REQUEST`                | JSON-RPC `-32600` | Missing `jsonrpc:"2.0"` / `method`; malformed envelope | Router P2.5 |
| `MCP_METHOD_NOT_FOUND`               | JSON-RPC `-32601` | Method outside §3.8.2 allow-list (S61) | Router P2.5 |
| `MCP_TOOL_NOT_FOUND`                 | JSON-RPC `-32602` (data.error_code) | `tools/call` with unknown `name` (S62) | Router P2.5 |
| `MCP_TOOL_INPUT_INVALID`             | JSON-RPC `-32602` (data.error_code) | `tools/call` arguments fail `inputSchema` validation (S63) | Router P2.5 |
| `MCP_TOOL_EXECUTION_FAILED`          | JSON-RPC `-32001` (data.error_code) | Underlying retrieval pipeline raises (S67) | Router P2.5 |
| `ES_PLUGIN_MISSING`                  | 503 (`/readyz`) | ES cluster missing `analysis-icu` plugin (B26, T0.8g) | Bootstrap / readyz |
| `ES_INDEX_MISSING`                   | 503 (`/readyz`) | A `resources/es/*.json` index is absent at boot | Bootstrap / readyz |
| `SCHEMA_DRIFT`                       | 503 (`/readyz`) + log `event=schema.drift` | Live schema differs from `schema.sql` / `resources/es/` | Bootstrap |
| `PIPELINE_TIMEOUT`                   | log `event=ingest.failed reason=pipeline_timeout` | Pipeline body exceeds `PIPELINE_TIMEOUT_SECONDS` (B18, S34) | Worker T3.2j |
| `ES_BBQ_UNSUPPORTED`                 | log `event=es.bbq_unsupported` | Cluster rejected `bbq_hnsw`; bootstrap retried with standard HNSW (B26) | Bootstrap |
| `RECONCILER_TICK_MISSING`            | Prometheus alert | `reconciler_tick_total` flat > 10 min (R8, S30) | Alerting rule T7.1a |
| `AUTH_TOKEN_EXPIRED`                 | 401             | JWT `exp` claim is in the past (T8.1) | Auth dependency T8.2 |
| `AUTH_CLAIM_MISSING`                 | 401             | `exp` or `preferred_username` claim absent or empty (T8.1) | Auth dependency T8.2 |
| `AUTH_TOKEN_INVALID`                 | 401             | JWT signature invalid, or `exp` non-numeric/non-integer (T8.1) | Auth dependency T8.2 |

### 4.2 Supported Formats

| Format | Converter | MIME (allow-list) | Notes | Phase |
|---|---|---|---|:---:|
| `.txt`  | `TextFileToDocument`     | `text/plain`              | UTF-8 text | **P1** |
| `.md`   | `MarkdownToDocument`     | `text/markdown`           | front-matter stripped | **P1** |
| `.html` | `HTMLToDocument`         | `text/html`               | visible text, script/style stripped | **P1** |
| `.csv`  | `CSVToDocument`          | `text/csv`                | row-as-document; rows packed by `RowMerger` to ~2 000 chars (B24); bounded by global 50 MB file limit (B2) | **P1** |
| `.pdf`  | `_PdfASTSplitter`        | `application/pdf`         | one atom per page; fast MuPDF text extraction; Tesseract OCR on image-bearing pages; batch-safe for large files | **P1** |
| `.docx` | `_DocxASTSplitter`       | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | paragraphs + tables (python-docx) | **P1** |
| `.pptx` | `_PptxASTSplitter`       | `application/vnd.openxmlformats-officedocument.presentationml.presentation` | one atom per slide (python-pptx) | **P1** |
| `.xlsx` | `XLSXToDocument`         | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | active sheets | P2 |

> 415 on unsupported MIME; 413 on > 50 MB. PDF ingest supports scanned / image-bearing pages via Tesseract OCR (requires OS-level `tesseract`); pages with no raster images use the fast MuPDF text path (no Tesseract cost).

### 4.3 Pipeline Catalog

| Pipeline | Components | Timeouts | Test Path | Phase |
|---|---|---|---|:---:|
| **Ingest** | `delete_by_document_id (idempotency) → FileTypeRouter → Converter → DocumentCleaner → LanguageRouter → {cjk_splitter \| en_splitter} (sentence-level, B1) → EmbeddingClient(bge-m3, batch=32) → ChunkRepository.bulk_insert → PluginRegistry.fan_out (per-plugin 60 s)` | Embedder 30 s/batch · ES bulk 60 s · MinIO get 30 s · plugin 60 s | `tests/integration/test_ingest_pipeline.py` | **P1** sync |
| **Chat** | `QueryEmbedder → ESVector(kNN on `embedding`, `bbq_hnsw` index, optional `term` filter on `source_app`/`source_meta` — B29 → B35) → ESBM25(multi_match `text`+`title^2`, `icu_text` analyzer, B26, same optional filter) → DocumentJoiner (C6 `CHAT_JOIN_MODE`: rrf\|concatenate\|vector_only\|bm25_only) → SourceHydrator(JOIN documents → returns full chunk content) → LLMClient.{chat\|stream}` (retrievers sequential in P1; parallel in P2 — see §3.4 P-A); router truncates `sources[].excerpt` to `EXCERPT_MAX_CHARS` (B23) | Embedder 10 s (single query) · ES query 10 s · LLM 120 s · per-batch ingest embed 30 s (asymmetric — query is one string, ingest is up to 32) | `tests/integration/test_chat_endpoint.py` (T3.9), `tests/integration/test_chat_stream_endpoint.py` (T3.11), `tests/integration/test_chat_pipeline_retrieval.py` (T3.5) | **P1** sync |
| **Retrieve** | Same as Chat pipeline up to `SourceHydrator` (shared `retrieval_pipeline` instance); no LLM call; router truncates `chunks[].excerpt` to `EXCERPT_MAX_CHARS` (B23); optional `dedupe` post-step (§3.4.4) | Embedder 10 s · ES query 10 s | `tests/unit/test_retrieve_router.py` (T3.19) | **P1** sync |

### 4.4 Plugin Catalog

| Plugin | `name` | `required` | `queue` | `extract()` | `delete()` | Phase |
|---|---|:---:|---|---|---|:---:|
| `VectorExtractor`    | `vector`     | ✓ | `extract.vector` | embed `f"{source_title}\n\n{chunk_text}"` (B15) → ES bulk index by `chunk_id`, denormalising `title`, `source_app`, `source_meta` onto each row (B15, B29 → B35) | ES bulk `_op_type=delete` | **P1** |
| `StubGraphExtractor` | `graph_stub` | — | `extract.graph`  | no-op | no-op | **P1** |
| `GraphExtractor`     | `graph`      | — | `extract.graph`  | LightRAG → Graph DB upsert | entity GC + ref_count | P3 |

### 4.5 Third-Party Client Catalog

| Client | Endpoint | Auth | Phase |
|---|---|---|:---:|
| `TokenManager` (×3 local / ×1 K8s) | `AI_API_AUTH_URL/auth/api/accesstoken` | J1 `{"key":…}` → J2 | **P1** |
| `EmbeddingClient` | `EMBEDDING_API_URL/text_embedding`              | J2 | **P1** |
| `LLMClient`       | `LLM_API_URL/gpt_oss_120b/v1/chat/completions` | J2 | **P1** |
| `RerankClient`    | `RERANK_API_URL/`                               | J2 | P1 unit / P2 wired |
| `HRClient`        | `HR_API_URL/v3/employees`                       | `Authorization` | P2 |

All 3rd-party calls: timeout/retry/backoff per `00_rule.md`; circuit-breaker on client.

**TokenManager refresh discipline (P-F):** each `TokenManager` instance has its own `threading.Lock`; concurrent callers around the `expiresAt − 5 min` boundary share one in-flight refresh per manager. Local mode: three independent managers (`AI_LLM/EMBEDDING/RERANK_API_J1_TOKEN`), each caching its own J2. K8s mode (`AI_USE_K8S_SERVICE_ACCOUNT_TOKEN=true`): one shared manager reads the SA token file per refresh and its J2 is shared across all three clients.

### 4.6 Environment Variables (C2 + B28)

> **Inventory rules (B28):** every external dependency, every per-call timeout, every operational threshold, and every credential MUST appear in this table. Code that reads a literal value not represented here is a spec drift bug. Vars marked `(required)` have no default and refuse boot.

> **v2 removed vars (C6):** `MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY/SECURE/BUCKET` (→ `MINIO_SITES`), `INGEST_MAX_FILE_SIZE_BYTES` (→ `INGEST_INLINE/FILE_MAX_BYTES`), `CHUNK_TARGET_CHARS_EN/CJK/CSV`, `CHUNK_OVERLAP_CHARS_EN/CJK/CSV`, `CHUNK_HARD_SPLIT_OVERLAP_CHARS`.

#### 4.6.1 Bootstrap & HTTP server

| Variable | Default | Description |
|---|---|---|
| `RAGENT_ENV`                          | (required)       | `dev` \| `staging` \| `prod`. P1 startup guard refuses non-`dev`. |
| `RAGENT_AUTH_DISABLED`                | `false`          | Must be `true` in P1; removed in P2 to enable JWT (§3.5). |
| `RAGENT_TRUST_X_USER_ID_HEADER`       | `false`          | **P2 only.** When `true` and `RAGENT_ENV != prod`, JWT dependency is bypassed and the `X-User-Id` header is trusted as `preferred_username` (§3.5). Strictly ignored in `prod`. |
| `RAGENT_PERMISSION_INGEST_ENABLED`    | `false`          | **P2 only.** When `true`, `GET/DELETE /ingest/v1/{id}` and `GET /ingest/v1` enforce `PermissionClient` (§3.5). Default off — gate is wired but inert until OpenFGA tuples exist. |
| `RAGENT_PERMISSION_CHAT_ENABLED`      | `false`          | **P2 only.** When `true`, chat retrieval applies the `PermissionClient` post-filter (§3.5). Default off. |
| `RAGENT_HOST`                         | `127.0.0.1`      | API bind address. P1 OPEN guard (§1) refuses any value other than `127.0.0.1` while `RAGENT_ENV=dev` & `RAGENT_AUTH_DISABLED=true`. |
| `RAGENT_PORT`                         | `8000`           | API bind port. |
| `LOG_LEVEL`                           | `INFO`           | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`. Applies to app + TaskIQ + Reconciler. |
| `CORS_ALLOW_ORIGINS`                  | *(unset)*        | Comma-separated list of allowed CORS origins (e.g. `https://app.example.com,https://admin.example.com`). When unset or empty, no `CORSMiddleware` is added and all cross-origin requests are denied. |

#### 4.6.2 Datastore connections (boot-blocking)

| Variable | Default | Description |
|---|---|---|
| `MARIADB_DSN`                         | (required)       | Full SQLAlchemy DSN, e.g. `mysql+aiomysql://user:pass@host:3306/ragent?charset=utf8mb4`. Used by repositories, bootstrap, `/readyz`. |
| `MARIADB_POOL_RECYCLE_SECONDS`        | `280`            | SQLAlchemy `pool_recycle` value. Connections older than this are discarded on checkout. Must be less than the server-side `wait_timeout`; default 280 s assumes a 300 s server timeout. |
| `ES_HOSTS`                            | (required)       | Comma-separated `https?://host:port` list. |
| `ES_USERNAME`                         | (optional)       | Basic-auth username; omit for unauthenticated dev clusters. |
| `ES_PASSWORD`                         | (optional)       | Basic-auth password. |
| `ES_API_KEY`                          | (optional)       | Alternative to user/password (mutually exclusive). |
| `ES_VERIFY_CERTS`                     | `true`           | Set `false` for self-signed dev clusters. |
| `ES_CHUNKS_INDEX`                     | `chunks_v1`      | Chunks index name. Threaded through `Container.chunks_index_name` to `ElasticsearchDocumentStore`, `_FeedbackMemoryRetriever`, `VectorExtractor`, `Reconciler`, and `/readyz` ES probe (T-EI.1). `init_es` also honours it when PUT-ing the `chunks_v1.json` schema, so override-and-rename works end-to-end (T-EI.6 / B60). Non-chunks resources (e.g. `feedback_v1.json`) keep filename-as-name semantics. |
| `MINIO_SITES`                         | (required)       | v2: JSON list of `{name, endpoint, access_key, secret_key, bucket, secure?, read_only?}`. Must include `name="__default__"` (inline ingest). Legacy single-site vars (`MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY/SECURE/BUCKET`) removed in v2 (see `v2 removed vars (C6)` callout at §4.6, or B37 in `docs/00_decisions.md`). |

#### 4.6.3 Redis (B27)

| Variable | Default | Description |
|---|---|---|
| `REDIS_MODE`                          | `standalone`     | `standalone` \| `sentinel`. Applies to broker and rate-limiter. |
| `REDIS_BROKER_URL`                    | `redis://localhost:6379/0` | TaskIQ broker URL (mode=standalone). |
| `REDIS_RATELIMIT_URL`                 | `redis://localhost:6379/1` | Rate-limiter URL (mode=standalone). |
| `REDIS_SENTINEL_HOSTS`                | (required if mode=sentinel) | Comma-separated `host:port` list (≥ 3 nodes recommended). |
| `REDIS_BROKER_SENTINEL_MASTER`        | `ragent-broker`  | Master name for broker instance (mode=sentinel). |
| `REDIS_RATELIMIT_SENTINEL_MASTER`     | `ragent-ratelimit` | Master name for rate-limiter instance (mode=sentinel). |

#### 4.6.4 Third-party API endpoints & credentials

| Variable | Default | Description |
|---|---|---|
| `AI_API_AUTH_URL`                     | (required)       | TokenManager J1→J2 endpoint (`POST /auth/api/accesstoken`). |
| `AI_LLM_API_J1_TOKEN`                 | (required, local) | J1 token for LLM service. POSTed as `{"key": value}`. **Never logged, never echoed.** |
| `AI_EMBEDDING_API_J1_TOKEN`           | (required, local) | J1 token for Embedding service. **Never logged, never echoed.** |
| `AI_RERANK_API_J1_TOKEN`              | (required, local) | J1 token for Rerank service. **Never logged, never echoed.** |
| `AI_USE_K8S_SERVICE_ACCOUNT_TOKEN`    | `false`          | When `true`, reads J1 from `/var/run/secrets/kubernetes.io/serviceaccount/token`; single shared J2 across all three services. Overrides the three `J1_TOKEN` vars. |
| `EMBEDDING_API_URL`                   | (required)       | bge-m3 endpoint. |
| `LLM_API_URL`                         | (required)       | gptoss-120b endpoint. |
| `RERANK_API_URL`                      | (required P2)    | Rerank endpoint (P1 unit-tests only; wired in P2). |
| `EMBEDDING_AUTH_HEADER_NAME`          | `Authorization`  | HTTP header name used by `EmbeddingClient`. Set to e.g. `X-API-Key` when the service does not use the `Authorization` header. Value sent is the raw J2 token (no `Bearer` prefix). |
| `LLM_AUTH_HEADER_NAME`                | `Authorization`  | HTTP header name used by `LLMClient`. Same semantics as `EMBEDDING_AUTH_HEADER_NAME`. |
| `RERANK_AUTH_HEADER_NAME`             | `Authorization`  | HTTP header name used by `RerankClient`. Same semantics as `EMBEDDING_AUTH_HEADER_NAME`. |
| `HR_API_URL`                          | (future)         | OpenFGA-related role lookup (P2+). |
| `UNPROTECT_ENABLED`                   | `false`          | When `true`, worker calls the unprotect API before passing file bytes to the ingest pipeline. |
| `UNPROTECT_API_URL`                   | (required when enabled) | Full URL of the unprotect endpoint (multipart POST). |
| `UNPROTECT_APIKEY`                    | (required when enabled) | Raw JWT (no `Bearer` prefix) sent as `apikey` request header. **Never logged, never echoed.** |
| `UNPROTECT_DELEGATED_USER_SUFFIX`     | (required when enabled) | Appended to `X-User-Id` to form the `delegatedUser` form field: `{X-User-Id}{suffix}`. |
| `EMBEDDING_REGISTRY_TTL_SECONDS`      | `10`             | B50 — TTL on the `ActiveModelRegistry` cache of `system_settings.embedding.*`. A cutover/rollback takes effect on the next App-cache refresh within this many seconds; the `dual_write_warmup` preflight gate refuses cutover until `2 × TTL` has elapsed since promote. |
| `COMMIT_MIN_HOURS`                    | `24`             | B50 — minimum observation window in `CUTOVER` state before `/embedding/v1/commit` is allowed (soft gate; override with `force=true`). Discourages impulsive commits that would retire the old stable field before issues surface. |

#### 4.6.5 Worker, Reconciler & retry policy

| Variable | Default | Description |
|---|---|---|
| `WORKER_HEARTBEAT_INTERVAL_SECONDS`   | `30`             | How often the worker refreshes `documents.updated_at` during pipeline body (B16). |
| `WORKER_MAX_ATTEMPTS`                 | `5`              | Pipeline gives up and marks `FAILED` once `attempt > WORKER_MAX_ATTEMPTS` (§3.1 R5). |
| `PIPELINE_TIMEOUT_SECONDS`            | `1800`           | Overall pipeline-body wall-clock ceiling (B18). |
| `RECONCILER_PENDING_STALE_SECONDS`    | `300`            | Re-dispatch threshold for `PENDING` rows whose heartbeat aged past this. |
| `RECONCILER_UPLOADED_STALE_SECONDS`   | `300`            | Re-kiq threshold for `UPLOADED` orphans (R1: TaskIQ message lost / broker outage at POST). |
| `RECONCILER_DELETING_STALE_SECONDS`   | `300`            | Resume threshold for stuck `DELETING` cascades. |

#### 4.6.6 Pipeline & chat tunables

| Variable | Default | Description |
|---|---|---|
| `INGEST_INLINE_MAX_BYTES`             | `10485760`       | v2: 10 MB cap on inline `content` UTF-8 byte length; 413 on overrun. |
| `INGEST_FILE_MAX_BYTES`                | `52428800`      | v2: 50 MB cap on file-type ingest size (HEAD-probe at API time); 413 on overrun. |
| `INGEST_LIST_MAX_LIMIT`               | `100`            | `GET /ingest/v1?limit=` upper bound (§4.1, B7). |
| `INGEST_MAX_ARCHIVE_MEMBERS`          | `5000`           | DOCX/PPTX zip-archive preflight: max entries in `infolist()`; 413 `INGEST_ARCHIVE_UNSAFE` on overrun (T-SEC.3/.4). |
| `INGEST_MAX_ARCHIVE_RATIO`            | `100`            | DOCX/PPTX zip-archive preflight: max `sum(file_size) / len(raw)` ratio; 413 `INGEST_ARCHIVE_UNSAFE` on overrun. |
| `INGEST_MAX_ARCHIVE_EXPANDED_BYTES`   | `524288000`      | DOCX/PPTX zip-archive preflight: 500 MB cap on `sum(file_size)` and per-member `file_size`; 413 `INGEST_ARCHIVE_UNSAFE` on overrun. |
| `INGEST_MAX_PDF_PAGES`                | `2000`           | PDF preflight: cap on `fitz.Document.page_count` before per-page extraction; 413 `INGEST_PDF_TOO_MANY_PAGES` on overrun (T-SEC.5/.6). |
| `CHUNK_TARGET_CHARS`                  | `1000`           | v2 `_BudgetChunker` target chars (mime-agnostic). |
| `CHUNK_MAX_CHARS`                     | `1500`           | v2 `_BudgetChunker` hard cap; atoms above this are hard-split. |
| `CHUNK_OVERLAP_CHARS`                 | `100`            | v2 `_BudgetChunker` overlap between adjacent chunks. |
| `EMBEDDER_BATCH_SIZE`                 | `32`             | Chunks per embedder HTTP call (P-B). |
| `CHAT_JOIN_MODE`                      | `rrf`            | `rrf` \| `concatenate` \| `vector_only` \| `bm25_only` (C6). |
| `CHAT_RERANK_ENABLED`                 | `true`           | Insert `_Reranker` between joiner and `_SourceHydrator` (F1). |
| `RETRIEVAL_TOP_K`                     | `20`             | Cap applied to retrievers, joiner, and reranker (F7). |
| `RETRIEVAL_MIN_SCORE`                 | *(unset)*        | Global default score floor for `/retrieve/v1` and `/chat/v1`; unset = no filtering (`null`). Must be >= 0.0 if set; boot fails otherwise. |
| `EXCERPT_MAX_CHARS`                   | `512`            | `_ExcerptTruncator` truncation length (B23). |
| `RAGENT_DEFAULT_LLM_PROVIDER`         | `openai`         | Echoed when request omits `provider`. |
| `RAGENT_DEFAULT_LLM_MODEL`            | `gptoss-120b`    | Echoed when request omits `model`. |
| `RAGENT_DEFAULT_LLM_TEMPERATURE`      | `0.7`            | |
| `RAGENT_DEFAULT_LLM_MAX_TOKENS`       | `4096`           | |
| `RAGENT_DEFAULT_SYSTEM_PROMPT`        | `You are a helpful assistant` | Auto-prepended when `messages` lacks a `system` entry. |
| `RAGENT_DEFAULT_RAG_SYSTEM_PROMPT`    | *(multi-intent template)*     | System prompt used when retrieval returns ≥1 doc and the caller has no `system` message. Contains grounding rules + QUESTION/SUMMARY/GENERATION intent styles with few-shot examples. No `{context}` placeholder — context is injected into the user message. |
| `RAGENT_RAG_GROUNDING_RULES`          | *(rules-only variant)*        | Rules-only system prompt prepended when the caller supplies their own `system` message alongside retrieved docs. Preserves the caller's persona while enforcing context-only grounding. |
| `CHAT_RATE_LIMIT_PER_MINUTE`          | `30`             | Per-user request cap on `/chat/v1` + `/chat/v1/stream` within the rate-limit window (B31). Excess returns 429 `CHAT_RATE_LIMITED`. |
| `CHAT_RATE_LIMIT_WINDOW_SECONDS`      | `60`             | Fixed-window length for `CHAT_RATE_LIMIT_PER_MINUTE` (B31). |
| `MCP_REQUEST_MAX_BYTES`               | `262144` (256 KiB) | Defence-in-depth cap on `POST /mcp/v1` request bodies; over-limit returns HTTP 413 `application/problem+json` (§3.8.1). |
| `CHAT_FEEDBACK_ENABLED`               | `false`          | Master switch for the feedback retrieval signal (B54). `true` enables `POST /feedback/v1`, the `_FeedbackMemoryRetriever` 3rd RRF input, and requires `FEEDBACK_HMAC_SECRET`. Default off — ship dark, observe write volume first (B57). |
| `CHAT_FEEDBACK_RRF_WEIGHT`            | `0.5`            | Weight on the feedback retriever's contribution in `DocumentJoiner(weights=[1.0, 1.0, this])` (B54). Cap < 1.0 to prevent popularity-loop dominance. |
| `CHAT_FEEDBACK_MIN_VOTES`             | `3`              | `(likes + dislikes)` threshold below which a (source_app, source_id) is dropped from the retriever (B54). Defeats single-user signal poisoning. |
| `CHAT_FEEDBACK_HALF_LIFE_DAYS`        | `14`             | Score decay half-life applied to the per-source Wilson score: `score × 0.5 ** (age_days / this)` (B54). |
| `FEEDBACK_HMAC_SECRET`                | *(required when `CHAT_FEEDBACK_ENABLED=true`)* | HMAC key for signing `/chat` response tokens and verifying `POST /feedback/v1` payloads (B55). Boot fails when feedback is enabled but the secret is unset. |
| `PDF_OCR_LANGUAGES`                   | `eng+chi_sim+chi_tra+jpn+deu` | Tesseract language pack list (plus-separated) used by `_PdfASTSplitter` when OCR-ing image-bearing pages. Install matching `tesseract-ocr-<lang>` packages if non-default languages are added. |

> **MCP protocol pins are NOT env-driven** — `protocolVersion` (`2024-11-05`) and `serverInfo.name` (`ragent`) are **pinned in spec §3.8.1 / B47** and live as module-level constants in `src/ragent/routers/mcp.py`. Operators flipping the protocol version would silently break the contract; the pin is intentional. The only MCP env knob is the body cap above.

#### 4.6.7 Per-call timeouts (matches §4.3 catalog)

| Variable | Default (s) | Site |
|---|---|---|
| `EMBEDDER_INGEST_TIMEOUT_SECONDS`     | `30`             | per-batch (32 strings) ingest call. |
| `EMBEDDER_QUERY_TIMEOUT_SECONDS`      | `10`             | single-string chat-query call (C8 asymmetric). |
| `ES_BULK_TIMEOUT_SECONDS`             | `60`             | `VectorExtractor` bulk index/delete. |
| `ES_QUERY_TIMEOUT_SECONDS`            | `10`             | chat retrievers (vector + BM25). |
| `MINIO_GET_TIMEOUT_SECONDS`           | `30`             | worker download from staging. |
| `MINIO_PUT_TIMEOUT_SECONDS`           | `60`             | router upload to staging. |
| `LLM_TIMEOUT_SECONDS`                 | `120`            | `LLMClient.{chat\|stream}`. |
| `PLUGIN_FAN_OUT_TIMEOUT_SECONDS`      | `60`             | per-plugin `extract`/`delete` ceiling (§3.3). |
| `READYZ_PROBE_TIMEOUT_SECONDS`        | `2`              | per-dependency `/readyz` probe budget (§4.1). |
| `UNPROTECT_TIMEOUT_SECONDS`           | `30`             | per-call budget for the unprotect API POST (when `UNPROTECT_ENABLED=true`). |

> Timeouts above are intentionally asymmetric: ingest embedder uses 30 s/batch (32 strings), query embedder uses 10 s (1 string) (C8). Same client, two call sites, two budgets.

#### 4.6.8 Observability (OpenTelemetry)

| Variable | Default | Description |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT`         | (optional)       | OTLP collector URL; absence disables export (no-op tracer). |
| `OTEL_SERVICE_NAME`                   | `ragent-api`     | Per-process: `ragent-api` \| `ragent-worker` \| `ragent-reconciler`. |
| `OTEL_TRACES_SAMPLER`                 | `parentbased_traceidratio` | Standard OTEL SDK sampler name. |
| `OTEL_TRACES_SAMPLER_ARG`             | `0.1`            | Sampling ratio (10% by default; raise to `1.0` in dev). |
| `HAYSTACK_TELEMETRY_ENABLED`          | `false`          | Disable Haystack anonymous usage analytics (PostHog). Set `false` for privacy/compliance. |
| `HAYSTACK_CONTENT_TRACING_ENABLED`    | `false`          | Include prompts and answers in OTEL spans. Keep `false` unless debugging; sensitive data. |
| `RAGENT_METRICS_SOURCE_APP_ALLOWLIST` | (empty)          | Comma-separated allow-list of `source_app` values that pass through verbatim as a Prometheus label. Anything outside the list is collapsed to `RAGENT_METRICS_SOURCE_APP_FALLBACK` to bound label cardinality. |
| `RAGENT_METRICS_SOURCE_APP_FALLBACK`  | `other`          | Bucket name for `source_app` values not in the allow-list. |
| `HTTP_ERROR_LOG_MAX_BYTES`            | `8192`           | Max bytes of request/response body included in `http.upstream_error` log records. Bodies above this size are truncated with `request_truncated` / `response_truncated` set to `true`. Sensitive headers (`Authorization`, `apikey`, `Cookie`, `X-API-Key`, `Proxy-Authorization`, plus the configured values of `EMBEDDING_AUTH_HEADER_NAME` / `LLM_AUTH_HEADER_NAME` / `RERANK_AUTH_HEADER_NAME`) and the J1 `key` field of the auth POST are always redacted regardless of size. |

---

## 5. Data Structures

### 5.1 MariaDB

> **v2 OVERRIDE** — `documents` adds `ingest_type ENUM('inline','file','upload') NOT NULL DEFAULT 'inline'`, `minio_site VARCHAR(64) NULL`, `source_url VARCHAR(2048) NULL`. The **`chunks` table is dropped** — chunks live only in ES `chunks_v1`. `object_key` semantics: for `inline`/`upload` it points into `__default__` MinIO site; for `file` it is the caller-supplied key in the named site (no copy). The third discriminator value `upload` was added by `migrations/011_ingest_type_upload.sql` to distinguish the multipart `POST /ingest/v1/upload` entry path from the JSON-body `inline` shape (different cleanup contract — see §3.1 table).

```sql
CREATE TABLE documents (
  document_id      CHAR(26)     PRIMARY KEY,
  create_user      VARCHAR(64)  NOT NULL,
  source_id        VARCHAR(128) NOT NULL,
  source_app       VARCHAR(64)  NOT NULL,
  source_title     VARCHAR(256) NOT NULL,
  source_meta      VARCHAR(1024) NULL,
  ingest_type      ENUM('inline','file','upload')  NOT NULL DEFAULT 'inline',
  minio_site       VARCHAR(64)   NULL,          -- NULL for inline (uses __default__ site)
  source_url       VARCHAR(2048) NULL,
  mime_type        VARCHAR(256)  NOT NULL,
  object_key       VARCHAR(256) NOT NULL,  -- MinIO key only (B10 format); bucket is config-driven (`MINIO_BUCKET`), not stored per-row (C3).
  status           ENUM('UPLOADED','PENDING','READY','FAILED','DELETING') NOT NULL,
  attempt          INT          NOT NULL DEFAULT 0,
  created_at       DATETIME(6)  NOT NULL,
  updated_at       DATETIME(6)  NOT NULL,
  INDEX idx_status_updated (status, updated_at),
  INDEX idx_source_app_id_status_created (source_app, source_id, status, created_at),
  INDEX idx_create_user_document (create_user, document_id)
);
-- (source_id, source_app) is the LOGICAL identity; uniqueness is eventual (supersede task), NOT a UNIQUE constraint.
-- create_user = audit only (NOT an authz field); idx_create_user_document supports "list my uploads" queries.

-- chunks table dropped in migration 003_drop_chunks.sql (v2). Chunks live in ES chunks_v1 only.

CREATE TABLE feedback (
  feedback_id     CHAR(26)     PRIMARY KEY,             -- new_id() (§5.3)
  request_id      CHAR(26)     NOT NULL,                -- echoed from /chat response
  user_id         VARCHAR(64)  NOT NULL,                -- X-User-Id at /chat time (signed into token)
  source_app      VARCHAR(64)  NOT NULL,                -- voted-source namespace (paired with source_id, B11/B35)
  source_id       VARCHAR(128) NOT NULL,                -- voted source (must be in shown_sources)
  vote            TINYINT      NOT NULL,                -- +1 like / -1 dislike
  reason          VARCHAR(32)  NULL,                    -- B56 enum (6 values) or NULL
  position_shown  SMALLINT     NULL,                    -- for future IPS (B57 item 1) — collected, unused in P1
  created_at      DATETIME(6)  NOT NULL,
  updated_at      DATETIME(6)  NOT NULL,
  UNIQUE KEY uq_user_req_app_src (user_id, request_id, source_app, source_id),
  CONSTRAINT ck_vote_unit CHECK (vote IN (-1, 1))
);
-- Append-only event log. ES `feedback_v1` (§5.4) is the derived serving view (B54/B55).
-- No content/text — query_text lives only in `feedback_v1` per the "text in ES, meta in MariaDB" rule.
```

No physical FK. ORM-level cascade only.

**ID classification:**
- **Internal IDs** (UID rule applies — `00_rule.md` §ID Generation Strategy): `document_id`, `chunk_id` — `CHAR(26)` UUIDv7→Crockford Base32, generated by `new_id()`.
- **External IDs / display fields** (UID rule does **not** apply — supplied by clients or upstream systems): `source_id` (≤ 128 chars; client-supplied stable identifier), `source_app` (≤ 64 chars; namespace/source system), `source_title` (≤ 256 chars; human-readable title surfaced as `sources[].source_title` in chat/retrieve responses), `source_meta` (optional free-format ≤ 1024 chars; renamed from `source_workspace` per B35), `create_user` (≤ 64 chars; audit metadata only, **not an authorization field**).
- The `task_id` returned from `POST /ingest` is the `document_id` itself; no separate task identifier exists.

### 5.2 Elasticsearch `chunks_v1`

> **v2 OVERRIDE** — adds `raw_content` field (`type: text, index: false`, `_source`-only). `content` (existing `text` column, may also be exposed under that legacy name) holds the **normalized** view embedded by bge-m3 + BM25-analyzed; `raw_content` holds the **original byte slice** the splitter captured (markdown fences, HTML tags, etc.). Chat retrieval scores against `content`, but the LLM context and `sources[].excerpt` use `raw_content` (with `content` fallback for legacy chunks). `source_url` is added as a `keyword` field for citation rendering.

> **Source of truth (B26):** `resources/es/chunks_v1.json` — settings + mappings, checked into git. Bootstrap (§6.1) reads this file and `PUT /chunks_v1` if the index does not exist. The block below is the canonical content; any drift between this spec snippet and the resource file is a CI failure (`tests/integration/test_es_resource_drift.py`).

```json
{
  "settings": {
    "index": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "default_pipeline": "chunks_default",
      "analysis": {
        "analyzer": {
          "icu_text": {
            "type": "custom",
            "tokenizer": "icu_tokenizer",
            "filter": ["icu_folding", "lowercase"]
          }
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "chunk_id":         { "type": "keyword" },
      "document_id":      { "type": "keyword" },
      "source_app":       { "type": "keyword" },
      "source_meta":      { "type": "keyword", "ignore_above": 1024 },
      "source_url":       { "type": "keyword" },
      "lang":             { "type": "keyword" },
      "title":            { "type": "text", "analyzer": "icu_text" },
      "text":             { "type": "text", "analyzer": "icu_text" },
      "raw_content":      { "type": "text", "index": false },
      "indexed_at":       { "type": "date" },
      "embedding": {
        "type": "dense_vector",
        "dims": 1024,
        "index": true,
        "similarity": "cosine",
        "index_options": { "type": "bbq_hnsw" }
      }
    }
  }
}
```

**Shard topology (B26):** `number_of_shards: 1` (single primary — sufficient for P1 corpus size, simpler routing); `number_of_replicas: 0` (no replicas — single-node dev/CI clusters reach `green` immediately; prod overrides via cluster-level template when HA is needed in P2).

**BM25 analyzer (B26):** `text` and `title` use the custom `icu_text` analyzer (`icu_tokenizer` + `icu_folding` + `lowercase`) — required for CJK tokenisation; the default `standard` analyzer collapses CJK to per-character or mega-tokens and breaks BM25. The `analysis-icu` plugin is a hard ES dependency, verified at `/readyz` (B26, I5). **Test override (B42):** integration tests run against a vanilla `elasticsearch:9.2.3` container without the plugin and load `tests/resources/es/chunks_v1.json` (standard analyzer) via the `RAGENT_ES_RESOURCES_DIR` env override; CJK BM25 behaviour is therefore not covered by integration tests and is validated by manual / staging smoke tests instead (see §7 Decision Log B42).

**Vector index (B26 → B58):** `embedding` uses `index_options.type = bbq_hnsw` — Better Binary Quantization HNSW (ES 8.16+) — for ~32× memory reduction at negligible recall cost. The earlier P1 choice (`flat`, exact brute-force kNN) was reversed in B58 (2026-05-19): with a 1024-dim corpus, `bbq_hnsw`'s residual recall loss sits well within chat retrieval's RRF tolerance, and the memory savings remove a Phase-2 migration step. **Test override:** `tests/resources/es/chunks_v1.json` keeps `flat` so vanilla `elasticsearch:9.2.3` CI containers stay light-weight; the structural-match invariant (`test_init_schema.py`) tolerates this delta alongside the B42 ICU delta.

**Default ingest pipeline `chunks_default` (B59):** `settings.index.default_pipeline = "chunks_default"` wires every chunk write through an ES ingest pipeline that fills the new `indexed_at` field from the coordinating-node clock — no Python writer touches the field (`docs/00_journal.md` 2026-05-19 Architecture row codifies the rule). Source of truth: `resources/es/pipelines/chunks_default.json`, mirrored below.

```json
{
  "description": "chunks_v1 default ingest pipeline (B59): fills indexed_at with the ES coordinating-node timestamp on every write — single source of truth for the chunk row's last-write time. Re-runs on every OVERWRITE.",
  "processors": [
    { "set": { "field": "indexed_at", "value": "{{{_ingest.timestamp}}}" } }
  ]
}
```

**Bootstrap order (B59):** `init_es()` MUST `PUT _ingest/pipeline/chunks_default` BEFORE `PUT chunks_v1` — ES rejects index creation when its `default_pipeline` references a missing pipeline (`tests/unit/test_init_schema.py::test_init_es_puts_pipelines_before_indexes` pins the ordering invariant). Pipeline PUT is idempotent on the ES side (overwrite by id), unlike index PUT which is guarded by HEAD.

**`indexed_at` semantics (B59):** the field reflects the **last** time a given `chunk_id` was successfully written to ES, not the first. Worker pipeline retries and B40 `DuplicatePolicy.OVERWRITE` re-runs the ingest pipeline on every overwrite, so `_ingest.timestamp` advances. `set` processor's `override: false` flag would not preserve first-write semantics under OVERWRITE — it inspects only the incoming `_source`, never the stored doc. Operators who need true creation time must read `documents.created_at` (MariaDB SoT).

**Title surface (B15):** `title` is denormalised onto every chunk row from `documents.source_title`. Two retrieval surfaces are derived from it:
1. **Lexical** — BM25 retriever runs `multi_match` on `["text", "title^2"]` (title boosted 2× over body) using the `icu_text` analyzer (B26).
2. **Semantic** — `embedding` is computed as `embed(f"{source_title}\n\n{chunk_text}")` at ingest time, so every chunk vector already carries title semantics. No separate `title_embedding` field is stored.

**Filter surface (B29 → B35):** `source_app` and `source_meta` are denormalised from `documents` onto every chunk row as `keyword` fields. Chat (§3.4.1) accepts optional `source_app` and `source_meta` filter params; when present they apply as ES `term` filters in **both** retrievers' `filter` clause (kNN `filter` and BM25 `bool.filter`). Filtering happens **before** scoring narrows the candidate pool, so top-K returned reflects the requested scope without over-fetch. These are **not auth fields** (B14): they are content-scope metadata, like `lang`. Permission gating remains a separate post-retrieval layer (§3.5).

### 5.3 ID / DateTime

- `new_id()` → UUIDv7 → Crockford Base32 → 26 chars (lexicographically sortable).
- `utcnow()` → tz-aware UTC. `to_iso()` → ISO 8601 `...Z`. `from_db(naive)` → attach UTC.

### 5.4 Elasticsearch `feedback_v1`

> **Source of truth (B26 pattern):** `resources/es/feedback_v1.json` — settings + mappings, checked into git. Bootstrap (§6.1) reads this file and `PUT /feedback_v1` if the index does not exist. Mirrors the `chunks_v1` pattern (§5.2): ES holds text + vector, MariaDB holds meta only.

```json
{
  "settings": {
    "index": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "analysis": {
        "analyzer": {
          "icu_text": { "type": "custom", "tokenizer": "icu_tokenizer",
                        "filter": ["icu_folding", "lowercase"] }
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "request_id":      { "type": "keyword" },
      "query_text":      { "type": "text",  "analyzer": "icu_text" },
      "query_embedding": { "type": "dense_vector", "dims": 1024,
                           "index": true, "similarity": "cosine",
                           "index_options": { "type": "flat" } },
      "source_id":       { "type": "keyword" },
      "source_app":      { "type": "keyword" },
      "source_meta":     { "type": "keyword", "ignore_above": 1024 },
      "vote":            { "type": "byte" },
      "reason":          { "type": "keyword" },
      "user_id_hash":    { "type": "keyword" },
      "ts":              { "type": "date" }
    }
  }
}
```

**Indexing semantics (B54/B55):** `_id = sha256(user_id|request_id|source_app|source_id)` (idempotent re-votes). `query_embedding` = `EmbeddingClient.embed([query_text], query=True)`. `user_id_hash = sha256(user_id)` (ES dump defence; plaintext in MariaDB only). Test override (B42) at `tests/resources/es/feedback_v1.json` omits ICU analyzer.

---

## 6. Standards

- **Layers:** Router (HTTP only) → Service (orchestration) → Repository (CRUD only).
- **Methods:** ≤ 30 LOC, max 2-level nesting. Utilities in `utility/`.
- **IDs:** UUIDv7 + Crockford Base32 (26 chars). **DateTime:** end-to-end UTC + `Z` suffix.
- **DB:** no physical FK; index every `WHERE / JOIN / ORDER BY` field.
- **Quality gate:** `uv run ruff format . && uv run ruff check . --fix && uv run pytest --cov=src/ragent --cov-branch --cov-fail-under=92` before every commit. **Test coverage floor: 92% (line + branch)** — CI rejects drops; DoD requirement.
- **TDD commits:** `[STRUCTURAL]` or `[BEHAVIORAL]` prefix; never mixed.
- **JSON naming convention (B21):** within request/response bodies, **identifier and resource fields are `snake_case`** (`document_id`, `source_id`, `source_app`, `source_title`, `error_code`, `next_cursor`, `task_id`, `trace_id`); **LLM token/config knobs are `camelCase`** (`maxTokens`, `promptTokens`, `completionTokens`, `totalTokens`, `temperature`, `topP` if added later) — preserved to match upstream OpenAI-shape expectations. Within a single body both styles may coexist; the rule above resolves which to use for any new field.

### 6.1 Schema & Migration (B3)

Two artefacts, **both versioned in git**, both consulted at boot:

| Artefact | Path | Purpose | Owner |
|---|---|---|---|
| **Consolidated snapshot** | `migrations/schema.sql` | Single-file DDL representing the current target schema. Updated **in lockstep with every incremental migration**. Used by fresh dev/CI/testcontainers bring-up (`mariadb < schema.sql` → instant ready). | Dev |
| **Incremental migrations** | `migrations/NNN_<slug>.sql` (e.g. `001_initial.sql`, `002_add_workspace.sql`) | Forward-only ALTER scripts applied via Alembic (`alembic upgrade head`). Production / staging path. | Dev |

**Boot-time auto-init (idempotent):**
- On startup, the bootstrap module runs `CREATE TABLE IF NOT EXISTS … / CREATE INDEX IF NOT EXISTS …` against MariaDB derived from `migrations/schema.sql`, and `PUT /<index>` for ES if the index does not exist — **using the JSON body in `resources/es/<index>.json`** (e.g. `resources/es/chunks_v1.json`, B26). Existing tables/indexes are left untouched.
- Resource files are the single source of truth for ES index definitions; spec §5.2 mirrors them in prose. `tests/integration/test_es_resource_drift.py` parses both and rejects drift.
- Auto-init is for dev/test bring-up convenience only; production migrations MUST go through Alembic (DB) or a controlled `PUT /<index>-vN` + reindex flow (ES). Boot-init refuses to run any `ALTER` or ES mapping update — schema drift is logged as `event=schema.drift` and surfaces in `/readyz` as a degraded state, not an automatic mutation.

**Invariant:** `schema.sql` ≡ replaying `001 → NNN`. CI enforces this with `tests/integration/test_schema_drift.py` (apply both paths to two scratch DBs, `mysqldump` both, diff must be empty).

### 6.2 Module Layout

> Canonical project tree. Every file is produced by exactly one Green/Structural plan row; no file is written outside this layout. Layered dependency rule: **routers → services → repositories**; **plugins / clients / storage / pipelines** are leaf concerns injected via the composition root (B30). **Only `bootstrap/composition.py` reads env vars** — every other module receives its config via constructor argument (B17, B30).

```
ragent/
├── pyproject.toml  .env.example  Dockerfile.es-test
├── deploy/k8s/reconciler-cronjob.yaml
├── migrations/  schema.sql  001_initial.sql  ...NNN_*.sql
├── resources/es/  chunks_v1.json  feedback_v1.json  pipelines/chunks_default.json
├── src/ragent/
│   ├── api.py  worker.py  reconciler.py
│   ├── bootstrap/  guard.py  broker.py  composition.py  init_schema.py  app.py
│   ├── routers/  ingest.py  chat.py  mcp.py  health.py
│   ├── services/  ingest_service.py
│   ├── repositories/  document_repository.py  chunk_repository.py
│   ├── plugins/  protocol.py  registry.py  vector.py  stub_graph.py
│   ├── pipelines/  factory.py  ingest.py  chat.py
│   ├── clients/  auth.py  embedding.py  llm.py  rerank.py  rate_limiter.py
│   ├── storage/  minio_client.py
│   ├── workers/  ingest.py  supersede.py
│   ├── schemas/  chat.py
│   ├── errors/  problem.py
│   ├── utility/  id_gen.py  datetime.py
│   └── state_machine.py
└── tests/  conftest.py  unit/  integration/  e2e/
```

**Module conventions:** No env reads outside `bootstrap/composition.py`. `@broker.task` decorators import from `ragent.bootstrap.broker` only. Plugins never import `pipelines/`, `routers/`, or HTTP layers. Routers → services → repositories (constructor-injected). `bootstrap/init_schema.py` applies `schema.sql` + `resources/es/*.json` idempotently; no inline DDL.

---

## 7. Decision Log

> Maintained in `docs/00_decisions.md` (append-only, B1–B60). Frozen 2026-05-04; changes require a new dated row.
