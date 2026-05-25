# Phase 1 v2 Completed Tracks

> Archived from docs/00_plan.md. All items [x].

## Track T-MCP — MCP JSON-RPC 2.0 Server (P2.5) — 2026-05-11

> Spec: `00_spec.md` §3.8 (B47). Implements `POST /mcp/v1` JSON-RPC 2.0 endpoint with five methods (`initialize`, `notifications/initialized`, `tools/list`, `tools/call`, `ping`) and one tool `retrieve` wrapping `POST /retrieve/v1`. P1 stub `POST /mcp/v1/tools/rag` is removed in T-MCP.12.

| # | Category | Task | Depends On | Status | Owner |
|---|---|---|---|:---:|---|
| T-MCP.1 | Red | • **Achieve:** Pin JSON-RPC 2.0 envelope contract (parse error, invalid request, method not found, notification).<br>• **Deliver:** `tests/unit/test_mcp_envelope.py` — (a) malformed JSON → 200 body `{jsonrpc:"2.0",id:null,error:{code:-32700,...}}`; (b) missing `jsonrpc`/`method` → -32600; (c) unknown method → -32601; (d) notification (`id` absent) → HTTP 204 empty body (S64/S65). | — | [x] | QA |
| T-MCP.2 | Green | • **Achieve:** Implement JSON-RPC dispatcher skeleton.<br>• **Deliver:** `src/ragent/routers/mcp.py` — `create_mcp_router()` returns router exposing `POST /mcp/v1`; envelope parsing + method dispatch table; envelope helpers `_jsonrpc_result(id, result)` / `_jsonrpc_error(id, code, message, data=None)`; `ping` handler returns `{}`. | T-MCP.1 | [x] | Dev |
| T-MCP.3 | Red | • **Achieve:** Pin `initialize` handshake (S58).<br>• **Deliver:** `tests/unit/test_mcp_initialize.py` — request `{method:"initialize", params:{protocolVersion:"2024-11-05", capabilities:{}, clientInfo:{...}}}` → result `{protocolVersion:"2024-11-05", capabilities:{tools:{}}, serverInfo:{name:"ragent", version:<semver>}}`. | T-MCP.2 | [x] | QA |
| T-MCP.4 | Green | • **Achieve:** Implement `initialize` handler.<br>• **Deliver:** `mcp.py::_handle_initialize(params)` returning the pinned `_MCP_PROTOCOL_VERSION="2024-11-05"` and `_MCP_SERVER_NAME="ragent"` module-level constants (B47 pins these; not env-driven). Server version reads `ragent.__version__`. | T-MCP.3 | [x] | Dev |
| T-MCP.5 | Red | • **Achieve:** Pin `tools/list` contract (S59) — exactly one tool `retrieve` with inputSchema matching §3.8.3.<br>• **Deliver:** `tests/unit/test_mcp_tools_list.py` — asserts `result.tools` length 1 and inputSchema deep-equals the spec literal (including `required:["query"]`, `top_k` bounds, etc.). | T-MCP.2 | [x] | QA |
| T-MCP.6 | Green | • **Achieve:** Implement `tools/list` returning the retrieve tool.<br>• **Deliver:** `mcp.py::_RETRIEVE_TOOL_SCHEMA` constant + `_handle_tools_list()`. | T-MCP.5 | [x] | Dev |
| T-MCP.7 | Red | • **Achieve:** Pin `tools/call retrieve` happy path (S60).<br>• **Deliver:** `tests/unit/test_mcp_tools_call_retrieve.py` — mock retrieval pipeline returns 3 chunks; request `{method:"tools/call", params:{name:"retrieve", arguments:{query:"q",top_k:3}}}` → `result.isError=false`, `result.content[0].type="text"`, `json.loads(result.content[0].text) == {chunks:[...]}` of length ≤ 3. | T-MCP.2 | [x] | QA |
| T-MCP.8 | Green | • **Achieve:** Implement `tools/call` dispatching to `run_retrieval`.<br>• **Deliver:** `mcp.py::_handle_tools_call` closure inside `create_mcp_router(retrieval_pipeline)`. | T-MCP.7 | [x] | Dev |
| T-MCP.9 | Red | • **Achieve:** Pin all `tools/call` error paths (S62, S63, S67) in one file.<br>• **Deliver:** `tests/unit/test_mcp_tools_call_errors.py`. | T-MCP.8 | [x] | QA |
| T-MCP.10 | Green | • **Achieve:** Add input schema validation + tool name dispatch + pipeline-failure mapper.<br>• **Deliver:** `mcp.py::_validate_retrieve_args(args)`. | T-MCP.9 | [x] | Dev |
| T-MCP.11 | Red | • **Achieve:** End-to-end through TestClient + real `build_retrieval_pipeline` (mocked components).<br>• **Deliver:** `tests/integration/test_mcp_router.py`. | T-MCP.4, T-MCP.6, T-MCP.10 | [x] | QA |
| T-MCP.12 | Refactor | • **Achieve:** Remove P1 stub endpoint and update docs.<br>• **Deliver:** delete `POST /mcp/v1/tools/rag` 501 route + its unit test; remove `MCP_NOT_IMPLEMENTED` from `HttpErrorCode`; `docs/API.md` documents `/mcp/v1` JSON-RPC. | T-MCP.11 | [x] | Dev |

---

## Track TA — aiomysql Adoption (async DB layer) — 2026-05-06

> Decision doc: `docs/team/2026_05_06_aiomysql_adoption.md`
> Goal: replace blocking `pymysql` + sync engine with `aiomysql` + `AsyncEngine` throughout the FastAPI/TaskIQ async path.

| # | Category | Task | Status | Owner |
|---|---|---|:---:|---|
| TA.1 | Red | • **Achieve:** Pin async contract for DocumentRepository and ChunkRepository. | [x] | QA |
| TA.2 | Green | • **Achieve:** Convert both repos to `async def` with SQLAlchemy `AsyncEngine`. | [x] | Dev |
| TA.3 | Red | • **Achieve:** Pin async IngestService contract. | [x] | QA |
| TA.4 | Green | • **Achieve:** Convert IngestService to async. | [x] | Dev |
| TA.5 | Red | • **Achieve:** Pin router drops `run_in_threadpool`. | [x] | QA |
| TA.6 | Green | • **Achieve:** Simplify ingest router to direct `await` calls. | [x] | Dev |
| TA.7 | Red | • **Achieve:** Pin reconciler fully-async contract. | [x] | QA |
| TA.8 | Green | • **Achieve:** Convert Reconciler to fully async. | [x] | Dev |
| TA.9 | Red | • **Achieve:** Pin ingest worker direct `await` on repos (no `to_thread.run_sync` for repo calls). | [x] | QA |
| TA.10 | Green | • **Achieve:** Refactor ingest worker to `await` repos directly + pipeline bridge via `anyio.from_thread.run`. | [x] | Dev |
| TA.11 | Green | • **Achieve:** Wire async engine in composition root + native async health probe. | [x] | Dev |
| TA.12 | Refactor | • **Achieve:** Green tests stay green after structural cleanup. | [x] | Reviewer |

---

## Phase 1 v2 — Ingest API breaking-change refactor (2026-05-06)

> Source of truth: `docs/team/2026_05_06_ingest_api_v2.md` (6/6 PASS).
> Supersedes the v1 ingest stack (multipart upload, lang-aware chunker,
> chunks DB table, single-MinIO env). Six TIDY-FIRST commits C1–C6.

### v2 task block (Track T2v)

| # | Category | Task | Commit | Status | Owner |
|---|---|---|:-:|:-:|---|
| T2v.20 | Structural | • **Achieve:** Add v2 documents columns + drop chunks table.<br>• **Deliver:** Alembic revision adding `ingest_type ENUM('inline','file')`, `minio_site VARCHAR(64) NULL`, `source_url VARCHAR(2048) NULL`; **DROP TABLE chunks**; schema-drift test green; `migrations/schema.sql` updated. | C2 | [x] | Dev |
| T2v.21 | Structural | • **Achieve:** Add ES `raw_content` field to `chunks_v1`.<br>• **Deliver:** `resources/es/chunks_v1.json` adds `"raw_content": {"type":"text","index":false,"doc_values":false}`; spec §5.2 sync; ES drift test green. | C2 | [x] | Dev |
| T2v.22 | Red  | • **Achieve:** Pin v2 request schema (discriminated union + validators). | C3 | [x] | QA |
| T2v.23 | Green | • **Achieve:** Implement Pydantic discriminated request models. | C3 | [x] | Dev |
| T2v.24 | Red  | • **Achieve:** Pin v2 router contract (JSON only, no multipart). | C3 | [x] | QA |
| T2v.25 | Green | • **Achieve:** Implement v2 router (JSON-only). | C3 | [x] | Dev |
| T2v.26 | Red  | • **Achieve:** Pin service `create` branching contract. | C3 | [x] | QA |
| T2v.27 | Green | • **Achieve:** Implement branched create + structured business log. | C3 | [x] | Dev |
| T2v.28 | Red  | • **Achieve:** Pin `MinioSiteRegistry` semantics. | C3 | [x] | QA |
| T2v.29 | Green | • **Achieve:** Implement registry + composition wiring. | C3 | [x] | Dev |
| T2v.30 | Red  | • **Achieve:** Pin `_TextLoader` Haystack component. | C4 | [x] | QA |
| T2v.31 | Green | • **Achieve:** Implement `_TextLoader` (~10 LOC). | C4 | [x] | Dev |
| T2v.32 | Red  | • **Achieve:** Pin `_MarkdownASTSplitter` (mistletoe). | C4 | [x] | QA |
| T2v.33 | Green | • **Achieve:** Implement `_MarkdownASTSplitter` via mistletoe AST walk. | C4 | [x] | Dev |
| T2v.34 | Red  | • **Achieve:** Pin `_HtmlASTSplitter` (selectolax). | C4 | [x] | QA |
| T2v.35 | Green | • **Achieve:** Implement `_HtmlASTSplitter` via selectolax DOM walk. | C4 | [x] | Dev |
| T2v.36 | Red  | • **Achieve:** Pin `_BudgetChunker` (mime-agnostic, 1000/1500/100). | C4 | [x] | QA |
| T2v.37 | Green | • **Achieve:** Implement `_BudgetChunker`. | C4 | [x] | Dev |
| T2v.38 | Red  | • **Achieve:** Pin `FileTypeRouter` wiring + unroutable failure path. | C4 | [x] | QA |
| T2v.39 | Green | • **Achieve:** Wire pipeline graph: `_TextLoader → FileTypeRouter → splitters → DocumentJoiner → _IdempotencyClean → _BudgetChunker → DocumentEmbedder → DocumentWriter(ES only)`. | C4 | [x] | Dev |
| T2v.40 | Red  | • **Achieve:** Pin chat read-path uses `raw_content` with `content` fallback. | C5 | [x] | QA |
| T2v.41 | Green | • **Achieve:** Implement chat read-path + `source_url` in citations. | C5 | [x] | Dev |
| T2v.42 | Red  | • **Achieve:** Pin per-step business + failure logs (Logging Rule extension). | C5 | [x] | QA |
| T2v.43 | Green | • **Achieve:** Wire structlog per-step events + correlate via OTEL. | C5 | [x] | Dev |
| T2v.44 | Refactor | • **Achieve:** Delete dead v1 code. | C6 | [x] | Dev |
| T2v.45 | Acceptance | • **Achieve:** Golden end-to-end test with wiremock embedding + testcontainers. | C6 | [x] | QA |

---

### Source-id review follow-up (Track T-SR, branch `claude/review-source-id-handling-dq6nj`)

| # | Category | Task | Commit | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-SR.1 | Behavioral | • **Achieve:** Cascade ES chunk delete when supersede picks a loser. | 13a2e28 | [x] | Dev |
| T-SR.2 | Structural | • **Achieve:** Capture the surrogate-PK + biz-UNIQUE rule and lock the revision-model decisions. | 934a478 / pending | [x] | Architect |
| T-SR.3 | Structural | • **Achieve:** Rename `documents.source_workspace` → `source_meta` and widen to `VARCHAR(1024)`. | pending | [x] | Dev |
| T-SR.4 | Behavioral | • **Achieve:** DB-side survivor election in `pop_oldest_loser_for_supersede`. | pending | [x] | Dev |
| T-SR.5 | Behavioral | • **Achieve:** Hydration surfaces only `READY` rows. | pending | [x] | Dev |
| T-SR.6 | Structural | • **Achieve:** Auto-create configured MinIO bucket(s) at boot. | pending | [x] | Dev |
| T-SR.7 | Structural | • **Achieve:** Split test tiers — `make test-gate` excludes e2e. | 2de1408 (main) | [x] | Dev |

---

### ICU analyzer convergence (Track T-ICU, T-ICU.1–T-ICU.3 completed; T-ICU.4 in main plan)

| # | Category | Task | Commit | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-ICU.1 | Structural | • **Achieve:** Reconcile spec §5.2 with B26. | eb7480a | [x] | Dev |
| T-ICU.2 | Red | • **Achieve:** Pin ICU analyzer in prod mapping; pin standard analyzer in test mapping. | 1cc791d | [x] | QA |
| T-ICU.3 | Green | • **Achieve:** Implement env-driven mapping dir + commit two mapping files. | 1cc791d | [x] | Dev |

---

### Reconciler-as-safety-net follow-up (Track T-RR, branch `claude/add-app-doctor-wPAZH`) — 2026-05-08

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-RR.1 | Red | • **Achieve:** Pin `_SourceHydrator` drop-on-miss semantics (B36 / S6j). | B36 | [x] | QA |
| T-RR.2 | Green | • **Achieve:** Implement drop-on-miss in hydrator. | B36 | [x] | Dev |
| T-RR.3 | Refactor | • **Achieve:** Update existing chat-pipeline tests. | B36 | [x] | Dev |
| T-RR.4 | Red | • **Achieve:** Pin composition no longer requires legacy `MINIO_ENDPOINT` vars when `MINIO_SITES` is set. | B37 | [x] | QA |
| T-RR.5 | Structural | • **Achieve:** Switch `/readyz` minio probe source from legacy `MinIOClient` to registry default site. | B37 | [x] | Dev |
| T-RR.6 | Green | • **Achieve:** Remove unconditional `_require` of legacy MinIO vars when `MINIO_SITES` is set. | B37 | [x] | Dev |
| T-RR.7 | Red | • **Achieve:** Pin AI token boot-time pre-warm. | B38 | [x] | QA |
| T-RR.8 | Green | • **Achieve:** Pre-warm tokens in lifespan startup. | B38 | [x] | Dev |
| T-RR.9 | Red | • **Achieve:** Pin worker's atomic promote-and-demote on READY (B39). | B39 | [x] | QA |
| T-RR.10 | Green | • **Achieve:** Implement atomic promote-demote in repository. | B39 | [x] | Dev |
| T-RR.11 | Red | • **Achieve:** Pin HTTP `DELETE /ingest/{id}` actually runs `fan_out_delete`. | B40 | [x] | QA |
| T-RR.12 | Structural | • **Achieve:** Inject `PluginRegistry` into `IngestService`. | B40 | [x] | Dev |
| T-RR.13 | Green | • **Achieve:** Replace `_has_fan_out` introspection with explicit registry call. | B40 | [x] | Dev |
| T-RR.14 | Red | • **Achieve:** Pin worker promote is DB-arbitrated by `MAX(created_at)`. | B41 | [x] | QA |
| T-RR.15 | Green | • **Achieve:** Implement DB-side survivor election in worker promote. | B41 | [x] | Dev |
| T-RR.16 | Red | • **Achieve:** Pin that post-READY enrichment (`fan_out`) does NOT run when the worker self-demotes. | B41 | [x] | QA |
| T-RR.17 | Green | • **Achieve:** Gate worker `fan_out` on promote outcome. | B41 | [x] | Dev |
| T-RR.18 | Red | • **Achieve:** Pin the `FOR UPDATE` lock semantic. | B41 | [x] | QA |

---

### Retrieve + Ingest API enhancements (branch `claude/add-ingest-filters-EIXOq`) — 2026-05-11

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-EF.1 | Behavioral | • **Achieve:** Add `top_k`/`min_score` request params to `POST /retrieve`; expose `source_meta` in chunk response; add Pydantic `response_model=` to all endpoints. | B7/B35 | [x] | Dev |
| T-EF.2 | Behavioral | • **Achieve:** Add `source_id`/`source_app` filter params to `GET /ingest` list; change list ordering to newest-first. | B7 | [x] | Dev |
| T-EF.3 | Behavioral | • **Achieve:** Fix `min_score` implementation — apply as post-retrieval filter. | B7 | [x] | Dev |

### top_k hard cap fix (branch `claude/fix-top-k-cap`) — 2026-05-11

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-EF.4 | Behavioral | • **Achieve:** Enforce `top_k` as a hard post-pipeline cap in `run_retrieval()`. | B7 | [x] | Dev |

### score field in retrieve response (branch `claude/fix-top-k-cap`) — 2026-05-11

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-EF.5 | Behavioral | • **Achieve:** Expose retrieval score in `POST /retrieve` chunk response. | §3.4.4 | [x] | Dev |

### API path versioning (branch `claude/add-api-versioning-7jm6C`) — 2026-05-11

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-AV.1 | Behavioral | • **Achieve:** Add `/v1` version segment to all business API paths. | §3.4 | [x] | Dev |

---

## Binary Document Loaders (DOCX / PPTX)

| # | Category | Task | Spec | Status | Owner |
|---|---|:-:|:-:|:-:|---|
| T-BL.1 | Red | • **Achieve:** Pin `_DocxASTSplitter` atom contract. | §4.2 | [x] | QA |
| T-BL.2 | Red | • **Achieve:** Pin `_PptxASTSplitter` atom contract. | §4.2 | [x] | QA |
| T-BL.3 | Green | • **Achieve:** Implement `_DocxASTSplitter` and `_PptxASTSplitter`. | §4.2 | [x] | Dev |
| T-BL.4 | Acceptance | • **Achieve:** `_MimeAwareSplitter` dispatch covers all new routes. | §4.2 | [x] | QA |
| T-BL.5 | Structural | • **Achieve:** Address Gemini/Codex PR review findings. | §4.2 | [x] | Dev |

### PPTX mime_type UX + pipeline error fix — 2026-05-12

| # | Category | Task | Spec | Status | Owner |
|---|---|:-:|:-:|:-:|---|
| T-BL.6 | Behavioral | • **Achieve:** Accept short aliases `pptx`/`docx` at all API entry points. | §4.1 | [x] | Dev |
| T-BL.7 | Behavioral | • **Achieve:** Reject binary MIME (DOCX/PPTX) on `ingest_type=inline` at schema validation time. | §4.1 | [x] | Dev |
| T-BL.8 | Behavioral | • **Achieve:** Worker uses `doc.mime_type` (DB) as authoritative MIME routing key. | §4.1 | [x] | Dev |
| T-BL.9 | Behavioral | • **Achieve:** Case-insensitive MIME handling per RFC 2045 §5.1. | §4.1 | [x] | Dev |
| T-BL.10 | Behavioral | • **Achieve:** Fix `mime_type=None` in all `ingest.step.*` structured logs for PPTX/DOCX uploads. | §T2v.42 | [x] | Dev |
| T-BL.11 | Behavioral | • **Achieve:** Log `file_size_bytes` in the load step and `splitter` name in the split step. | §T2v.42 | [x] | Dev |
| T-BL.12 | Behavioral | • **Achieve:** Ensure `mime_type` appears in all `ingest.step.*` logs for legacy rows. | §T2v.42 | [x] | Dev |

---

### Ingest pipeline file-type bug fixes — 2026-05-12

| # | Category | Task | Spec | Status | Owner |
|---|---|:-:|:-:|:-:|---|
| T-FIL.1 | Behavioral | • **Achieve:** Fix `head_object` `or 0` bug. | §3.1 | [x] | Dev |
| T-FIL.2 | Behavioral | • **Achieve:** Enforce `INGEST_FILE_MAX_BYTES` limit for `ingest_type=file` ingests. | §4.2 | [x] | Dev |
| T-FIL.3 | Behavioral | • **Achieve:** Replace `SELECT … FOR UPDATE` with lock-free atomic correlated-subquery UPDATE. | §B39 | [x] | Dev |
| T-FIL.4 | Behavioral | • **Achieve:** Verify `ingest_type=file` worker never calls `delete_object`. | §3.1 | [x] | Dev |
| T-FIL.5 | Behavioral | • **Achieve:** Fix `_record_file` false `ObjectNotFoundError` for files with unknown size metadata. | §3.1 | [x] | Dev |
| T-FIL.6 | Behavioral | • **Achieve:** Guard `_log_transition("PENDING", "DELETING")` on actual row change. | §B39 | [x] | Dev |

---

### Unprotect API integration (branch `claude/add-unprotect-api-integration-coBow`) — 2026-05-13

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-UP.1 | Red | • **Achieve:** Pin `UnprotectClient` contract. | §4.6.4 | [x] | QA |
| T-UP.2 | Red | • **Achieve:** Pin worker unprotect-gate behaviour. | §4.6.4 | [x] | QA |
| T-UP.3 | Green | • **Achieve:** Implement `UnprotectClient` and wire it into the composition root and worker. | §4.6.4 | [x] | Dev |

---

### PDF ingest support (branch `claude/add-pdf-ingest-support-bghaI`) — 2026-05-13

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-PDF.1 | Red | • **Achieve:** Pin `_PdfASTSplitter` atom contract. | §4.2 | [x] | QA |
| T-PDF.2 | Green | • **Achieve:** Implement `_PdfASTSplitter` and helper `_pdf_page_text`. | §4.2 | [x] | Dev |
| T-PDF.3 | Green | • **Achieve:** Wire `application/pdf` end-to-end through schema, factory, and existing tests. | §4.2, §4.6 | [x] | Dev |
| T-PDF.4 | Refactor | • **Achieve:** Address post-review findings: remove redundant batch loop, make OCR language list env-configurable. | §4.6 | [x] | Dev |
| T-PDF.5 | Green | • **Achieve:** Implement PyMuPDF best-practice OOM prevention. | §4.2 | [x] | Dev |

---

### Manual rerun endpoint (branch `claude/add-rerun-ingest-endpoint-SEriU`) — 2026-05-14

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-RERUN.1 | Red+Green | • **Achieve:** Add `DocumentRepository.mark_for_rerun(document_id)`. | §3.1 / S41 | [x] | Dev |
| T-RERUN.2 | Red+Green | • **Achieve:** Add `IngestService.rerun(document_id)` and `DocumentNotRerunnable` exception. | §3.1 / S41 | [x] | Dev |
| T-RERUN.3 | Red+Green | • **Achieve:** Add `POST /ingest/v1/{document_id}/rerun` returning 202 / 404 / 409. | §4.1 / §4.1.2 / S41 | [x] | Dev |

---

### HTTP upstream error logging (T-HTTPLOG.1–T-HTTPLOG.2 completed; T-HTTPLOG.3 in main plan)

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-HTTPLOG.1 | Red | • **Achieve:** Pin `install_error_logging(client, ...)` contract. | §4.6.8 | [x] | QA |
| T-HTTPLOG.2 | Green | • **Achieve:** Implement the hook factory and wire it into both shared httpx clients. | §4.6.8 | [x] | Dev |

---

### Security file-upload checks (branch `claude/security-file-upload-checks-ac4Cv`) — 2026-05-14

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-SEC.1 | Red | • **Achieve:** Pin magic-byte rejection at `POST /ingest/v1/upload`. | §4.2 | [x] | QA |
| T-SEC.2 | Green | • **Achieve:** Implement magic-byte validator at upload route. | §4.2 | [x] | Dev |
| T-SEC.3 | Red | • **Achieve:** Pin zip-archive preflight contract for DOCX/PPTX. | §4.2 | [x] | QA |
| T-SEC.4 | Green | • **Achieve:** Implement `assert_safe_zip` and wire it into DOCX/PPTX splitters. | §4.2, §4.6 | [x] | Dev |
| T-SEC.5 | Red | • **Achieve:** Pin PDF page-count cap before per-page extraction. | §4.2 | [x] | QA |
| T-SEC.6 | Green | • **Achieve:** Implement page-count guard in `_PdfASTSplitter` + env var. | §4.2, §4.6 | [x] | Dev |
| T-SEC.7 | Behavioral | • **Achieve:** Expose Prometheus counter for guard rejections. | §4.6 | [x] | Dev |
| T-SEC.8 | Refactor | • **Achieve:** Update spec + env-var inventory. | §4.2, §4.6 | [x] | Dev |

---

### Replace Tesseract OCR with RapidOCR (branch `claude/enable-pdf-ocr-GVQBy`) — 2026-05-21

| ID | Phase | Deliver | Spec | Status | Owner |
|---|---|---|---|---|---|
| T-OCR.1 | Red | Update OCR tests to mock `_get_rapidocr_engine`. | §4.2 | [x] | QA |
| T-OCR.2 | Green | Add `rapidocr-onnxruntime`; rewrite `_pdf_page_text()` to use RapidOCR. | §4.2 | [x] | Dev |
| T-OCR.3 | Refactor | Update spec + remove `PDF_OCR_LANGUAGES` env-var row. | §4.6 | [x] | Dev |
| T-OCR.4 | Refactor | Use `pymupdf4llm.to_markdown` per page; remove `_rapidocr_engine` singleton. | §4.2 | [x] | Dev |

---

### Header/footer exclusion — PDF + PPTX (branch `claude/strip-header-footer-pdf-pptx`)

| ID | Phase | Description | Spec | Status | Owner |
|----|-------|-------------|------|--------|-------|
| T-HDR.1 | Behavioral | PDF: add `INGEST_PDF_MARGIN_PTS`. | §4.2 | [x] | Dev |
| T-HDR.2 | Behavioral | PPTX: filter `PP_PLACEHOLDER.FOOTER / DATE / SLIDE_NUMBER` shapes. | §4.2 | [x] | Dev |

---

### Embedding-model lifecycle (branch `claude/design-embedding-model-switch-5N3Uh`) — 2026-05-15

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-EM.0 | Analysis | Lock the multi-vector single-index swap design and APIs. | B50 | [x] | Architect |
| T-EM.1 | Red | Pin the embedding-lifecycle state machine. | B50 | [x] | QA |
| T-EM.2 | Green | Implement the embedding-lifecycle state machine. | B50 | [x] | Dev |
| T-EM.3 | Red | Pin `EmbeddingModelConfig` dataclass. | B50 | [x] | QA |
| T-EM.4 | Green | Implement `EmbeddingModelConfig`. | B50 | [x] | Dev |
| T-EM.5 | Structural | Persist lifecycle settings in MariaDB. | B50 | [x] | Dev |
| T-EM.6 | Red | Pin `SystemSettingsRepository` contract. | B50 | [x] | QA |
| T-EM.7 | Green | Implement repository. | B50 | [x] | Dev |
| T-EM.8 | Red | Pin `ActiveModelRegistry` cache contract. | B50 | [x] | QA |
| T-EM.9 | Green | Implement `ActiveModelRegistry`. | B50 | [x] | Dev |
| T-EM.10 | Red | Pin cutover preflight. | B50 | [x] | QA |
| T-EM.11 | Green | Implement preflight. | B50 | [x] | Dev |
| T-EM.12 | Red | Pin admin router for five lifecycle endpoints. | B50 | [x] | QA |
| T-EM.13 | Green | Implement admin router. | B50 | [x] | Dev |
| T-EM.14 | Red | Pin ingest dual-write. | B50 | [x] | QA |
| T-EM.15 | Green | Implement dual-write embedder. | B50 | [x] | Dev |
| T-EM.16 | Red | Pin query path uses `registry.read_model()`. | B50 | [x] | QA |
| T-EM.17 | Green | Implement dynamic field selection in `_QueryEmbedder`. | B50 | [x] | Dev |
| T-EM.18 | Red | Pin retired-field reconciler arm. | B50 | [x] | QA |
| T-EM.19 | Green | Implement reconciler arm. | B50 | [x] | Dev |
| T-EM.20 | Red | End-to-end lifecycle integration test. | B50 | [x] | QA |
| T-EM.21 | Green | Wire `ActiveModelRegistry` into composition root. | B50 | [x] | Dev |

### Embedding lifecycle rework — index-per-model + alias cutover (branch `claude/clarify-embedding-fields-Ir2sW`) — 2026-05-21

| Task | Type | Dep | Status | Owner |
|---|---|---|---|---|
| T-EM-R.1 | Red+Green | T-EM.4, T-EM.9 | [x] | Dev |
| T-EM-R.2 | Red+Green | T-EM-R.1 | [x] | Dev |
| T-EM-R.3 | Red+Green | T-EM-R.1 | [x] | Dev |
| T-EM-R.4 | Red+Green | T-EM-R.3 | [x] | Dev |
| T-EM-R.5 | Red+Green | T-EM-R.1 | [x] | Dev |
| T-EM-R.6 | Red+Green | T-EM-R.1 | [x] | Dev |
| T-EM-R.7 | Red+Green | T-EM-R.1 | [x] | Dev |
| T-EM-R.8 | Red+Green | T-EM-R.1 | [x] | Dev |
| T-EM-R.9 | Red+Green | T-EM-R.6 | [x] | Dev |
| T-EM-R.10 | Red+Green | T-EM-R.3, T-EM-R.4, T-EM-R.9 | [x] | QA |

---

### Feedback retrieval signal (branch `claude/investigate-rrf-retrieval-pDFt4`) — 2026-05-16

| Task | Type | Dep | Status | Owner |
|---|---|---|---|---|
| T-FB.1 | Red+Green | B55 | [x] | Dev |
| T-FB.2 | Red+Green | B54 | [x] | Dev |
| T-FB.3 | Structural | B54 | [x] | Dev |
| T-FB.4 | Red+Green | T-FB.3 | [x] | QA / Dev |
| T-FB.5 | Structural | B54 | [x] | Dev |
| T-FB.6 | Red+Green | T-FB.1, T-FB.4, T-FB.5 | [x] | QA / Dev |
| T-FB.7 | Red+Green | T-FB.2, T-FB.5 | [x] | QA / Dev |
| T-FB.8 | Structural | T-FB.4, T-FB.7 | [x] | Dev |
| T-FB.9 | Red+Green | T-FB.7, T-FB.8 | [x] | QA / Dev |
| T-FB.10 | Red+Green | T-FB.1 | [x] | QA / Dev |
| T-FB.11 | Red+Green | T-FB.6, T-FB.9, T-FB.10 | [x] | QA |
| T-FB.12 | Refactor | T-FB.11 | [x] | Dev |

---

### Ingest upload discriminator fix (branch `claude/fix-ingest-upload-inline-XEUBM`) — 2026-05-19

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-IUP.1 | Red | • **Achieve:** Pin the new discriminator + cleanup contract on the service layer. | §3.1 | [x] | QA |
| T-IUP.2 | Green | • **Achieve:** Add the `upload` enum value end-to-end and wire the new cleanup gate. | §3.1 | [x] | Dev |

---

### ES chunks index config + housekeeping (T-EI track — 100% complete)

| Task | Type | Dep | Status | Owner |
|---|---|---|---|---|
| T-EI.1 | Structural | — | [x] | Dev |
| T-EI.2 | Red | — | [x] | QA |
| T-EI.2a | Red+Green | — | [x] | Dev |
| T-EI.3 | Green | T-EI.2 | [x] | Dev |
| T-EI.4 | Red+Green | T-EI.3 | [x] | QA |
| T-EI.5 | Spec | T-EI.3 | [x] | Spec |
| T-EI.6 | Red+Green | PR #83 review | [x] | Dev |

---

### API pipeline param sanity & per-stage observability (branch `claude/api-pipeline-logging-bfjkJ`) — 2026-05-19

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-APL.1 | Red | Pin per-request `top_k` reaches `_Reranker.run` and `_FeedbackMemoryRetriever.run`. | §3.4.1 | [x] | QA |
| T-APL.2 | Green | Thread per-request `top_k` to both components. | T-APL.1 | [x] | Dev |
| T-APL.3 | Red | Pin explicit-`0` constructor kwargs on clients are honoured. | §4.6 | [x] | QA |
| T-APL.4 | Green | Replace `value or env_default` with `value if value is not None else env_default`. | T-APL.3 | [x] | Dev |
| T-APL.5 | Structural | Move module-level env reads to composition root; inject as constructor kwargs. | §4.6 | [x] | Dev |
| T-APL.6 | Red | Pin chat pipeline component observability events. | §4.4 | [x] | QA |
| T-APL.7 | Green | Extract `wrap_component_run` into generic `wrap_pipeline_component` helper. | T-APL.6 | [x] | Dev |
| T-APL.8 | Red | Pin `request_id` propagation across TaskIQ boundary. | §4.4 | [x] | QA |
| T-APL.9 | Green | Implement `taskiq.TaskiqMiddleware` subclass for context propagation. | T-APL.8 | [x] | Dev |
| T-APL.10 | Structural | Drop `wrap_component_run` back-compat alias. | T-APL.7 | [x] | Dev |
| T-APL.11 | Red+Green | Each wrapped `run()` opens an OTEL span. | T-APL.7, T-APL.10 | [x] | Dev |

---

### Inline ingest skips unprotect + unprotect failure fallback (branch `claude/inline-ingest-unprotect-DkCl4`) — 2026-05-21

| Task | Type | Dep | Status | Owner |
|---|---|---|---|---|
| T-UP.4 | Red | T-UP.3 | [x] | QA |
| T-UP.5 | Green | T-UP.4 | [x] | Dev |
