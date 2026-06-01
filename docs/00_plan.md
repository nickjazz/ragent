# 00_plan.md — Master TDD Implementation Checklist

> Source: `docs/00_spec.md` · Authored: 2026-05-03 · Reorg: 2026-05-04 (Round 3)
> Workflow: `CLAUDE.md` §THE TDD WORKFLOW · Tidy First (`[STRUCTURAL]` / `[BEHAVIORAL]`)
> Each `[ ]` = one Red→Green→Refactor cycle, each cycle = one (or two) commit.
> Reorg driver: `docs/team/2026_05_04_phase1_round3_reorg_auth_off.md` (12/12 6-of-6).

## Status legend
- `[x]` delivered
- `[ ]` TODO
- `[~]` descoped / deferred (not doing in this cycle)

---

> Phase 1 tracks (T0-T8): [docs/completed_plan/phase1_tracks.md](completed_plan/phase1_tracks.md)
> Phase 1 v2 completed tracks: [docs/completed_plan/phase1_v2_tracks.md](completed_plan/phase1_v2_tracks.md)

---

## Phase 1 → Phase 2 spillover

> Two acceptance gates promised in P1 cannot run inside the per-PR CI gate as-is — both need infrastructure (live AI endpoints / slow-job chaos lane) that is P2-owned. Recorded here so DoD §200 stays an absolute invariant for tracks T0–T7 and the spillovers stay visible.

| # | Category | Task | Depends On | Status | Owner | Phase |
|---|---|---|---|:---:|---|:---:|
| T7.3.x | Acceptance | • **Achieve:** Wire the T7.3 retrieval-recall SLO to a real automated gate against live embedder/rerank/LLM endpoints (closes the gap noted in `docs/00_journal.md::E2E gate integrity`).<br>• **Deliver:** (a) Scheduled / label-triggered CI job that exports real `EMBEDDING_API_URL` / `LLM_API_URL` / `RERANK_API_URL` + tokens via secrets, runs `make test-e2e-golden`; (b) decision-log row pinning which endpoint identity is used + cost expectation per run; (c) `tests/e2e/test_golden_set.py::test_golden_set_top3_accuracy_at_least_70pct` xfail flips to a hard assertion on that job. Default WireMock e2e remains the per-PR gate. | T7.3 | [~] | QA | P2 |
| T7.4.x | Acceptance | • **Achieve:** Replace the single happy-path chaos test (currently xfail run=False, `tests/e2e/test_chaos_worker_kill.py`) with a partial-failure suite covering the cross-storage failure modes that motivated the test.<br>• **Deliver:** decomposed into Track T-CHAOS rows C1–C6 below; this row stays for traceability and flips `[x]` when all six are green. | T5.6, T7.4 | [~] | SRE | P2 |

---

## Track T-CHAOS — Chaos Drill Suite (P2.6 軌三 / T7.4.x) — 2026-05-11

> Spec: `00_spec.md` §3.6.1 (B49). Each case under `tests/e2e/test_chaos/test_C<N>_<scenario>.py`, marked `@pytest.mark.docker`, gated by nightly CI lane (not per-PR). Common asserts per §3.6.1: terminal status; ES/DB consistency; OTEL spans; `chaos_drill_outcome_total{case,outcome}` increment.

| # | Category | Task | Depends On | Status | Owner |
|---|---|---|---|:---:|---|
| T-CHAOS.0 | Structural | • **Achieve:** Establish chaos suite scaffold and pin fixture-reuse policy.<br>• **Deliver:** `tests/e2e/test_chaos/__init__.py` + `tests/e2e/test_chaos/conftest.py` — shared fixtures: WireMock reset between cases, `chaos_drill_outcome_total` metric scrape helper. **Fixture-reuse policy** (avoid 6× ~30s testcontainer boot tax): C3/C4/C5/C6 (WireMock-only injection) reuse session-scoped `running_stack` from `tests/e2e/conftest.py`; C1/C2 (worker kill / split-brain) use function-scope `spawn_module` because they kill the worker process. `chaos_drill_outcome_total` counter added to `src/ragent/bootstrap/metrics.py` with labels `case`, `outcome`. | T7.4 | [x] | SRE |
| T-CHAOS.C1 | Red+Green | • **Achieve:** Validate worker SIGKILL recovery (existing test unblocked).<br>• **Deliver:** Move `tests/e2e/test_chaos_worker_kill.py` → `tests/e2e/test_chaos/test_C1_worker_sigkill.py` ✓; lift `xfail(run=False)` and write the four `@spec §3.6.1` assertions ✓; xfail(strict=True) lifted on 2026-05-14 after `_claim` was rewritten as `_atomic_claim`. See journal SRE 2026-05-14 "Recovery semantics closeout". | T-CHAOS.0 | [x] | SRE |
| T-CHAOS.C2 | Red+Green | • **Achieve:** Validate worker crash between MariaDB commit and ES bulk leaves a recoverable state.<br>• **Deliver:** `tests/e2e/test_chaos/test_C2_db_es_split.py` — monkeypatch worker `_commit_ready` to raise `ConnectionError` post-DB-commit, pre-ES-bulk; restart worker; assert reconciler heals via R3 multi-READY-repair or worker retry; final state READY with ES chunks present. | T-CHAOS.0 | [x] | SRE |
| T-CHAOS.C3 | Red+Green | • **Achieve:** Validate ES bulk 207 partial failure is retried idempotently.<br>• **Deliver:** `tests/e2e/test_chaos/test_C3_es_bulk_207.py` — `DocumentEmbedder._run_dual` checks `errors:true` in bulk response, logs `es.bulk_partial_failure` per failed item, retries only failed items in a second bulk call. Mock-based in-process test; `call_count == 2` confirmed. | T-CHAOS.0 | [x] | SRE |
| T-CHAOS.C4 | Red+Green | • **Achieve:** Validate rerank 5xx fail-open (depends on P2.3 reranker wired).<br>• **Deliver:** `tests/e2e/test_chaos/test_C4_rerank_5xx.py` — WireMock `/rerank` returns 500 for 3 consecutive calls; chat returns 200 with RRF-ordered sources; `rerank_degraded_total{reason="5xx"}+=3`. Blocked on P2.3. | P2.3 | [x] | SRE |
| T-CHAOS.C5 | Red+Green | • **Achieve:** Validate LLM stream interrupt emits `data: {type:"error",...}` per B6.<br>• **Deliver:** `tests/e2e/test_chaos/test_C5_llm_stream_interrupt.py` — `LLMStreamInterruptedError` added; `_do_stream` tracks `seen_done`; raises on EOF without `[DONE]`; `stream()` never retries on interrupt; chat router reads `error_code` dynamically from exc. WireMock injects 3 deltas no `[DONE]`; last SSE frame `error_code==LLM_STREAM_INTERRUPTED`. | T-CHAOS.0 | [x] | SRE |
| T-CHAOS.C6 | Red+Green | • **Achieve:** Validate MinIO transient 503 is retried (3×@2s built-in).<br>• **Deliver:** `tests/e2e/test_chaos/test_C6_minio_503.py` — `get_object` retries `ConnectionError`/`S3Error` transients up to `MINIO_GET_RETRIES` (default 3); logs `minio.transient_error` on each retry; re-raises client errors (`NoSuchKey`/`AccessDenied`) immediately. Mock test verifies 2 warnings + 3 call attempts. | T-CHAOS.0 | [x] | SRE |
| T-CHAOS.7 | Structural | • **Achieve:** Wire nightly CI lane for chaos suite.<br>• **Deliver:** `.github/workflows/chaos-nightly.yml` runs `pytest tests/e2e/test_chaos -m docker` on a `cron: '0 3 * * *'` schedule (03:00 UTC); `Makefile` target `make test-chaos`; nightly artefact retains test logs for 30 days via `actions/upload-artifact`. | T-CHAOS.C1–C6 | [x] | SRE |
| T7.4.x | Closure | • **Achieve:** Flip the spillover row when all six cases green for ≥ 3 consecutive nightly runs.<br>• **Deliver:** plan.md row `T7.4.x` → `[x]` with evidence (nightly run links). | T-CHAOS.7 | [~] | SRE |

---

## Track TA — aiomysql Adoption — 100% complete → see [docs/completed_plan/phase1_v2_tracks.md](completed_plan/phase1_v2_tracks.md)

---

## Phase 2 — Production Quality (+3 weeks) — *complete (delivered items [x]; descoped items [~])*

| # | Category | Task | Status | Owner |
|---|---|---|:---:|---|
| P2.1 | Stability | • **Achieve:** Production-grade HA + observability.<br>• **Deliver:** SRE HA verification report (`docs/ha_runbook.md`), Grafana dashboard (`deploy/grafana/ragent_overview.json`), alerting rules (`deploy/prometheus/alerts.yaml` — 4 new alerts). | [x] | SRE |
| P2.2 | Security | • **Achieve:** Activate JWT + Permission layer per Track T8; B14 invariant (ES carries no auth fields).<br>• **Deliver:** All `[~]` rows in T8 → `[ ]` → `[x]`; remove `RAGENT_AUTH_DISABLED`; introduce `RAGENT_TRUST_X_USER_ID_HEADER` (default `false`) and per-surface `RAGENT_PERMISSION_INGEST_ENABLED` / `RAGENT_PERMISSION_CHAT_ENABLED` (both default `false`) — wiring lands but enforcement stays opt-in. | [~] | Dev |
| P2.3 | Behavioral | • **Achieve:** Improve chat ranking via reranker.<br>• **Deliver:** `RerankClient` wired into chat pipeline as `HybridRetrieverWithRerank` SuperComponent. Reranker wiring was completed in P1; P2.3 delivers fail-open resilience: `UpstreamServiceError` / `UpstreamTimeoutError` → log `rerank.degraded` + increment `rerank_degraded_total{reason}` + return RRF-ordered docs[:top_k]. | [x] | Dev |
| P2.4 | Behavioral | • **Achieve:** Route translate/summarize intents to direct LLM, bypassing retrieval.<br>• **Deliver:** `ConditionalRouter` intent split. | [~] | Dev |
| P2.5 | Behavioral | • **Achieve:** Replace P1 501 stub with real MCP JSON-RPC 2.0 server exposing the `retrieve` tool (B47, §3.8).<br>• **Deliver:** decomposed into Track T-MCP rows; flips `[x]` when T-MCP.1–T-MCP.12 are all `[x]`. | [x] | Dev |
| P2.6 | Quality | • **Achieve:** Continuous answer-quality + load resilience evidence.<br>• **Deliver:** RAGAS eval in CI; large-file streaming; chaos drills (軌三 decomposed into Track T-CHAOS, B49). | [~] | QA |
| P2.7 | Behavioral | • **Achieve:** Concurrent component execution for ingest/chat.<br>• **Deliver:** Switch ingest/chat to Haystack `AsyncPipeline`. | [~] | Dev |
| P2.8 | Closure | • **Achieve:** Close P2 with synced docs and lessons.<br>• **Deliver:** Updated `00_spec.md` (C4 pinned, alert table extended, P2.7 deferred note, §3.4 P-A ref removed) / `00_plan.md` (descope pass) + new entries in `00_journal.md` (P2.3 reranker fail-open design, P2/P3 descope audit). | [x] | Master |
| P2.9 | Stability | • **Achieve:** Close prior MinIO orphan-sweeper idea as not-doing.<br>• **Deliver:** MinIO objects are retained for audit/replay; no TTL sweeper is installed. | [x] | SRE |

## Phase 3 — Graph Enhancement (conditional, +4–6 weeks) — *gated*

| # | Category | Task | Status | Owner |
|---|---|---|:---:|---|
| P3.1 | Decision | • **Achieve:** Lock graph DB choice with a written rationale.<br>• **Deliver:** ADR for Graph DB selection (Neo4j Community / ArcadeDB / Memgraph). | [~] | Architect |
| P3.2 | Behavioral | • **Achieve:** Replace stub with a real graph extractor on the same Protocol.<br>• **Deliver:** `GraphExtractor` implementation replacing `StubGraphExtractor`. | [~] | Dev |
| P3.3 | Behavioral | • **Achieve:** Add graph retrieval branch to chat pipeline.<br>• **Deliver:** `HybridRetrieverWithGraph` SuperComponent + `LightRAGRetriever` (200 ms TO → []). | [~] | Dev |
| P3.4 | Governance | • **Achieve:** Govern entity lifecycle in the graph store.<br>• **Deliver:** Entity soft-delete + ref_count + GC + reconciliation cron. | [~] | Dev |
| P3.5 | Gate | • **Achieve:** Confirm graph track is justified before spend.<br>• **Deliver:** Gate decision: P2 stable ≥ 4 weeks AND hybrid alone underperforms on relational queries. | [~] | PM |

---

## Completed P1 v2 sub-tracks summary

> All rows [x]. Full task text in [docs/completed_plan/phase1_v2_tracks.md](completed_plan/phase1_v2_tracks.md).

- **T-MCP** (T-MCP.1–T-MCP.12) — MCP JSON-RPC 2.0 Server — 100% complete
- **T2v** (T2v.20–T2v.45) — Phase 1 v2 Ingest API refactor — 100% complete
- **T-SR** (T-SR.1–T-SR.7) — Source-id review follow-up — 100% complete
- **T-ICU.1–T-ICU.3** — ICU analyzer convergence (T-ICU.4 below) — 100% complete
- **T-RR** (T-RR.1–T-RR.18) — Reconciler-as-safety-net follow-up — 100% complete
- **T-EF** (T-EF.1–T-EF.5), **T-AV.1** — Retrieve/Ingest enhancements + versioning — 100% complete
- **T-BL** (T-BL.1–T-BL.12) — Binary Document Loaders (DOCX/PPTX) — 100% complete
- **T-FIL** (T-FIL.1–T-FIL.6) — Ingest pipeline bug fixes — 100% complete
- **T-UP** (T-UP.1–T-UP.3) — Unprotect API integration — 100% complete
- **T-PDF** (T-PDF.1–T-PDF.5) — PDF ingest support — 100% complete
- **T-RERUN** (T-RERUN.1–T-RERUN.3) — Manual rerun endpoint — 100% complete
- **T-HTTPLOG.1–T-HTTPLOG.3** — HTTP upstream error logging — 100% complete
- **T-SEC** (T-SEC.1–T-SEC.8) — Security file-upload checks — 100% complete
- **T-OCR** (T-OCR.1–T-OCR.4) — Replace Tesseract with RapidOCR — 100% complete
- **T-HDR** (T-HDR.1–T-HDR.2) — Header/footer exclusion — 100% complete
- **T-EM** (T-EM.0–T-EM.21) + **T-EM-R** (T-EM-R.1–T-EM-R.10) — Embedding-model lifecycle — 100% complete
- **T-FB** (T-FB.1–T-FB.12) — Feedback retrieval signal — 100% complete
- **T-IUP** (T-IUP.1–T-IUP.2) — Ingest upload discriminator fix — 100% complete
- **T-EI (T-EI.1–T-EI.2 + T-EI.2a + T-EI.3–T-EI.6)** — ES chunks index config — 100% complete
- **T-APL** (T-APL.1–T-APL.11) — API pipeline param sanity & observability — 100% complete
- **T-UP.4–T-UP.5** — Inline ingest unprotect fix — 100% complete

---

## Open items in partially-complete tracks

### ES embedding field name clarification — T-EF-CLEAN (complete)

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-EF-CLEAN.1 | Structural | • **Achieve:** Fix misleading `_QueryEmbedder` docstring that claimed registry mode targets `embedding_<m>_<d>` fields. Correct to: always emits `embedding_field="embedding"`; alias flip handles lifecycle cutover (B61). | B61 | [x] | Dev |
| T-EF-CLEAN.2 | Structural | • **Achieve:** Remove dead `_REGISTRY_MODEL_FIELD` constant and the `PUT /_mapping` block from `tests/integration/test_chat_pipeline_retrieval.py::es_store`. That setup installed `embedding_testmodel_1024` on `chunks_v1` under the abandoned field-per-model design (B50); it was never queried because `_QueryEmbedder` hardcodes `"embedding"`. Update `_stub_registry` docstring to reflect the correct index-per-model design. | B61 | [x] | Dev |
| T-EF-CLEAN.3 | Structural | • **Achieve:** Document index-per-model design supersession of B50 in `docs/spec/decision_log.md` (B61). Also backfill missing B60 entry (`ES_CHUNKS_INDEX` overridability). Update `docs/00_spec.md` §7 range marker to B1–B61. | B61 / B60 | [x] | Dev |

---

### ICU analyzer convergence — T-ICU.4 (remaining)

| # | Category | Task | Commit | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-ICU.4 | Acceptance | • **Achieve:** Manual / staging smoke test for CJK BM25 (S36 coverage gap from B42).<br>• **Deliver:** Documented procedure: operator runs `Dockerfile.es-test` ES, applies prod mapping, indexes a `"產品規格"` doc, verifies `_analyze` tokenises into `["產品", "規格"]` and BM25 query recalls. Tracked as a release-gate manual step; not blocking pre-commit. | T-ICU.3 | [ ] | Ops |

---

### HTTP upstream error logging — T-HTTPLOG.3 (remaining)

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-HTTPLOG.3 | Refactor | • **Achieve:** /simplify + /review pass; journal-add row in `docs/00_journal.md` (Spec) recording the deliberate `http_request_payload` / `http_response_payload` denylist carve-out for upstream-error diagnostics. | §4.6.8 | [x] | Dev |

---

### MCP Hub microservice — T-MH (100% complete)

> Source: user kickoff. Standalone FastMCP service that loads `tools.yaml` at startup and dynamically registers each REST endpoint as an MCP Tool. Streamable HTTP transport.

| # | Category | Task | Spec | Status | Owner |
|---|---|---|:-:|:-:|---|
| T-MH.0 | Kickoff | • **Achieve:** Land the dynamic Hub skeleton — YAML schema, signature factory, httpx forwarder, lifespan-managed client, Streamable HTTP entry point.<br>• **Deliver:** `src/ragent/mcp_hub/{mcp_hub.py,server.py,tools.example.yaml,__init__.py}` + `tests/unit/mcp_hub/test_signature_factory.py`. | — | [x] | Dev |
| T-MH.1 | Spec | • **Achieve:** Document the Hub microservice in `docs/00_spec.md` — tools.yaml schema, env-var inventory, Streamable HTTP endpoint contract, deployment topology. | — | [x] | Spec |
| T-MH.2 | Test | • **Achieve:** Add an integration test that boots the Hub against a stub upstream and exercises one tool call over Streamable HTTP via a FastMCP client. | — | [x] | QA |
| T-MH.3 | Hardening | • **Achieve:** Pre-compute per-tool wire dicts (header-name kebab map, partitioned param lists) and connection limits from `defaults`; consider auth header pass-through.<br>• **Deliver:** updates to `src/ragent/mcp_hub/mcp_hub.py`. | — | [x] | Dev |
| T-MH.4 | Behavioral | • **Achieve:** Upstream-error transparency contract — replace blanket `raise_for_status` with structured envelopes. | — | [x] | Dev |
| T-MH.5 | Behavioral | • **Achieve:** Static `tools.yaml` validator runnable in CI. | — | [x] | Dev |
| T-MH.6 | Behavioral | • **Achieve:** Address gemini-code-assist PR #79 review (three medium-priority findings). | — | [x] | Dev |
| T-MH.7 | Behavioral | • **Achieve:** Heterogeneous-upstream support — per-tool `base_url` override, per-tool `static_headers`, per-tool `forward_headers`. | — | [x] | Dev |
| T-MH.8a | Behavioral | • **Achieve:** Header model rework — drop `${ENV_VAR}` substitution; flip `forward_headers` schema to template strings. | — | [x] | Dev |
| T-MH.8b | Behavioral | • **Achieve:** Multi-system directory loading with per-system isolation. | — | [x] | Dev |
| T-MH.9 | Behavioral | • **Achieve:** Operator-facing structured logging via `structlog`. | — | [x] | Dev |
| T-MH.10 | Behavioral | • **Achieve:** Expose the project's own `POST /retrieve/v1` as an MCP tool by default. | — | [x] | Dev |
| T-MH.11 | Behavioral | • **Achieve:** Operability triple — per-system `verify_ssl`, Hub serves `GET /metrics`, `LoadFailure` carries structured fields. | — | [x] | Dev |
| T-MH.12 | Behavioral | • **Achieve:** Expose `build_mcp_app()` as a 0-arg uvicorn `--factory` entry point; refactor `main()` to delegate to it; extend `bool_env()` to accept `"on"` sentinel; update K8s api command to uvicorn CLI; update docs. | — | [x] | Dev |

---

## Track T-CH — Chat Intent Detection + `retrieve` Flag

> Source: 2026-05-26 feature request.
> Adds LLM-based intent classification before retrieval and an explicit `retrieve` flag.
> Intent → `requires_retrieve` mapping lives in `src/ragent/routers/chat.py`; system prompt gains a "根據資料" opener rule for retrieval-grounded intents.

| # | Category | Task | Status | Owner |
|---|---|---|:---:|---|
| T-CH.D1 | Red+Green | • **Achieve:** `_requires_retrieve()` maps all known intents correctly.<br>• **Deliver:** `tests/unit/test_chat_intent.py::test_requires_retrieve_known_intents` — GREETING/CHITCHAT → False; QUESTION/SUMMARY/GENERATION → True. | [x] | Dev |
| T-CH.D2 | Red+Green | • **Achieve:** `_requires_retrieve()` defaults unknown labels to True (fail-safe).<br>• **Deliver:** `tests/unit/test_chat_intent.py::test_requires_retrieve_unknown_defaults_true`. | [x] | Dev |
| T-CH.D3 | Red+Green | • **Achieve:** `_detect_intent()` returns correct label when LLM returns exact match.<br>• **Deliver:** `tests/unit/test_chat_intent.py::test_detect_intent_known_label` — mock LLM returns "GREETING" → "GREETING". | [x] | Dev |
| T-CH.D4 | Red+Green | • **Achieve:** `_detect_intent()` falls back to QUESTION for unrecognised LLM output.<br>• **Deliver:** `tests/unit/test_chat_intent.py::test_detect_intent_unknown_label_fallback`. | [x] | Dev |
| T-CH.D5 | Red+Green | • **Achieve:** `_detect_intent()` falls back to QUESTION on LLM exception.<br>• **Deliver:** `tests/unit/test_chat_intent.py::test_detect_intent_exception_fallback`. | [x] | Dev |
| T-CH.D6 | Red+Green | • **Achieve:** `_detect_intent()` uses only the first word of multi-word LLM output.<br>• **Deliver:** `tests/unit/test_chat_intent.py::test_detect_intent_multiword_uses_first_word`. | [x] | Dev |
| T-CH.R1 | Red+Green | • **Achieve:** `build_rag_messages(inject_context=False)` passes messages through without `<context>` wrapping.<br>• **Deliver:** `tests/unit/test_build_rag_messages.py::test_inject_context_false_no_context_tag` — system prompt still prepended. | [x] | Dev |
| T-CH.R2 | Red+Green | • **Achieve:** `build_rag_messages(inject_context=False)` still floats caller system messages to front.<br>• **Deliver:** `tests/unit/test_build_rag_messages.py::test_inject_context_false_system_floated`. | [x] | Dev |
| T-CH.R3 | Red+Green | • **Achieve:** `ChatRequest.retrieve` field defaults True and accepts False.<br>• **Deliver:** `tests/unit/test_build_rag_messages.py::test_chat_request_retrieve_field`. | [x] | Dev |
| T-CH.P1 | Red+Green | • **Achieve:** `_RAG_COMMON_INSTRUCTIONS` contains the GROUNDED RESPONSE OPENER rule.<br>• **Deliver:** `tests/unit/test_build_rag_messages.py::test_system_prompt_contains_grounded_opener_rule`. | [x] | Dev |
| T-CH.I1 | Red+Green | • **Achieve:** `POST /chat/v1 {retrieve:false}` skips intent detection + pipeline; `sources=[]`.<br>• **Deliver:** `tests/integration/test_chat_endpoint.py::test_retrieve_false_skips_pipeline`. | [x] | Dev |
| T-CH.I2 | Red+Green | • **Achieve:** `POST /chat/v1/stream {retrieve:false}` done frame has `sources=[]`.<br>• **Deliver:** `tests/integration/test_chat_endpoint.py::test_stream_retrieve_false_sources_empty`. | [x] | Dev |
| T-CH.I3 | Red+Green | • **Achieve:** `POST /chat/v1` with intent=GREETING skips retrieval pipeline; `sources=[]`.<br>• **Deliver:** `tests/integration/test_chat_endpoint.py::test_greeting_intent_skips_retrieval`. | [x] | Dev |
| T-CH.I4 | Red+Green | • **Achieve:** `POST /chat/v1` with intent=QUESTION still runs retrieval pipeline.<br>• **Deliver:** `tests/integration/test_chat_endpoint.py::test_question_intent_runs_retrieval`. | [x] | Dev |

---

## Track T-CH2 — context_mode, per-intent temperature, prompt selection by intent

> Source: 2026-05-26 design session (follows T-CH).
> Replaces `retrieve: bool` with `context_mode: Literal["auto","caller","force"]`;
> adds per-intent temperature (`_INTENT_TEMPERATURE`); decouples citation rules from system
> prompt so `[N]` references only appear when the system injected the context.

**Design matrix** (`context_mode` × `intent`):

| context_mode | intent | retrieve | inject_context | prompt | sources |
|---|---|---|---|---|---|
| auto | GREETING/CHITCHAT | skip | False | _PLAIN_ASSISTANT | null |
| auto | QUESTION/SUMMARY/GENERATION | run | True | _DEFAULT_RAG (with [N]) | []/[{...}] |
| caller | GREETING/CHITCHAT | skip | False | _PLAIN_ASSISTANT | null |
| caller | QUESTION/SUMMARY/GENERATION | skip | False | _RAG_NO_CITATION (no [N]) | null |
| force | any intent | run | True | _DEFAULT_RAG (with [N]) | []/[{...}] |

**Temperature** (intent-based, used when `body.temperature is None`):
`GREETING/CHITCHAT → 0.8`, `QUESTION/SUMMARY → 0.2`, `GENERATION → 0.7`

| # | Category | Task | Status | Owner |
|---|---|---|:---:|---|
| T-CH2.S1 | Red+Green | • **Achieve:** `ChatRequest.context_mode` replaces `retrieve: bool`.<br>• **Deliver:** `tests/unit/test_chat_request_schema.py::test_context_mode_*` — defaults "auto", accepts "caller"/"force", rejects invalid; update T-CH.R3 test. | [x] | Dev |
| T-CH2.S2 | Red+Green | • **Achieve:** `ChatRequest.temperature` becomes `float \| None = None` (None = use intent-based auto).<br>• **Deliver:** `tests/unit/test_chat_request_schema.py::test_temperature_none_accepted`. | [x] | Dev |
| T-CH2.S3 | Red+Green | • **Achieve:** `build_rag_messages(intent=GREETING, inject_context=False)` uses `_PLAIN_ASSISTANT_PROMPT`.<br>• **Deliver:** `tests/unit/test_build_rag_messages.py::test_plain_prompt_for_greeting_no_context`. | [x] | Dev |
| T-CH2.S4 | Red+Green | • **Achieve:** `build_rag_messages(intent=QUESTION, inject_context=True)` prompt contains `[N]` citation rules.<br>• **Deliver:** `tests/unit/test_build_rag_messages.py::test_rag_prompt_has_citation_when_inject_context`. | [x] | Dev |
| T-CH2.S5 | Red+Green | • **Achieve:** `build_rag_messages(intent=QUESTION, inject_context=False)` prompt has NO `[N]` citation rules.<br>• **Deliver:** `tests/unit/test_build_rag_messages.py::test_no_citation_prompt_when_caller_context`. | [x] | Dev |
| T-CH2.R1 | Red+Green | • **Achieve:** `_INTENT_TEMPERATURE` maps all intents; unknown defaults to `_DEFAULT_TEMPERATURE`.<br>• **Deliver:** `tests/unit/test_chat_intent.py::test_intent_temperature_mapping`. | [x] | Dev |
| T-CH2.R2 | Red+Green | • **Achieve:** `context_mode="caller"` always skips retrieval regardless of intent.<br>• **Deliver:** `tests/unit/test_chat_intent.py::test_caller_mode_always_skips_retrieval`. | [x] | Dev |
| T-CH2.R3 | Red+Green | • **Achieve:** `context_mode="force"` always runs retrieval regardless of intent (even GREETING).<br>• **Deliver:** `tests/unit/test_chat_intent.py::test_force_mode_always_runs_retrieval`. | [x] | Dev |
| T-CH2.R4 | Red+Green | • **Achieve:** Intent detection always runs regardless of `context_mode`.<br>• **Deliver:** `tests/integration/test_chat_endpoint.py::test_intent_detection_runs_for_all_context_modes`. | [x] | Dev |
| T-CH2.I1 | Red+Green | • **Achieve:** `context_mode="caller"` + QUESTION intent: `sources=null`, no `<context>` injection, no `[N]` in outgoing system prompt.<br>• **Deliver:** `tests/integration/test_chat_endpoint.py::test_caller_mode_no_citation_in_prompt`. | [x] | Dev |
| T-CH2.I2 | Red+Green | • **Achieve:** `temperature=null` + GREETING intent: LLM called with `_INTENT_TEMPERATURE["GREETING"]`.<br>• **Deliver:** `tests/integration/test_chat_endpoint.py::test_auto_temperature_greeting`. | [x] | Dev |
| T-CH2.I3 | Red+Green | • **Achieve:** `context_mode="force"` + GREETING intent: retrieval runs, sources populated.<br>• **Deliver:** `tests/integration/test_chat_endpoint.py::test_force_mode_retrieval_runs`. | [x] | Dev |

### Post-merge fix — PR #130 review (2026-05-27)

| # | Category | Fix |
|---|----------|-----|
| PR130.F1 | Defensive | `result.get("content") or ""` in sync handler — guard against null/missing LLM content field (KeyError on safety-filtered responses). Gemini review comment. |

---

## Track T-AM — Auth Mode Consolidation

> Replace two-boolean auth config (`RAGENT_AUTH_DISABLED` + `RAGENT_TRUST_X_USER_ID_HEADER`) with a single `RAGENT_AUTH_MODE` enum.
> New modes: `none` (no header required, `create_user="anonymous"`) and `jwt_prefer_header` (JWT fallback to `X-User-Id`).
> New JWT verification flags: `RAGENT_JWT_VERIFY_AUD` + `RAGENT_JWT_VERIFY_EXP`.

| # | Category | Task | Status | Owner |
|---|---|---|:---:|---|
| T-AM.S1 | Structural | • **Achieve:** `AuthMode` enum + `parse_auth_mode()` as the single source of truth for mode resolution.<br>• **Deliver:** `src/ragent/bootstrap/auth_mode.py` — `AuthMode(str, Enum)` with values `none \| user_header \| jwt_header \| jwt_prefer_header`; `parse_auth_mode()` reads `RAGENT_AUTH_MODE` (default `user_header`); raises `ValueError` on unknown value. Tests: `tests/unit/test_auth_mode_parse.py`. | [x] | Dev |
| T-AM.1 | Behavioral | • **Achieve:** Guard enforces `RAGENT_AUTH_MODE` rules, replacing old two-bool logic.<br>• **Deliver:** Rewrite `src/ragent/bootstrap/guard.py`; rewrite `tests/unit/test_bootstrap_startup_guard.py` — `none`/`user_header`/`jwt_prefer_header` → `dev` only; `jwt_header` → no env restriction; `jwt_header`/`jwt_prefer_header` → require `OIDC_DOMAIN` + `OIDC_AUDIENCE`. | [x] | Dev |
| T-AM.2 | Behavioral | • **Achieve:** Middleware + composition handle all 4 modes; `none` injects `"anonymous"`, skips header check; `jwt_prefer_header` tries JWT first, falls back to `X-User-Id`.<br>• **Deliver:** Update `app.py` middleware, `composition.py` JWT-manager guard, `openapi.py` `is_trust_header_mode()`. Tests: extend `tests/unit/test_bootstrap_app_middleware.py` (or create). | [x] | Dev |
| T-AM.3 | Behavioral | • **Achieve:** `RAGENT_JWT_VERIFY_AUD` (default `true`) + `RAGENT_JWT_VERIFY_EXP` (default `true`) respected by JWT verifier; both `false` require `RAGENT_ENV=dev`.<br>• **Deliver:** Guard checks flags; `build_token_manager()` receives `verify_aud` + `verify_exp`; joserfc claims options updated. Tests: `tests/unit/test_jwt_verify_flags.py`. | [x] | Dev |
| T-AM.S2 | Structural | • **Achieve:** Remove `RAGENT_AUTH_DISABLED` + `RAGENT_TRUST_X_USER_ID_HEADER` from all source, tests, and docs.<br>• **Deliver:** Delete dead reads in `guard.py`, `app.py`, `composition.py`, `openapi.py`; update `docs/spec/env_vars.md`, `docs/00_spec.md`, `.env.example` if present. | [x] | Dev |

---

## Track T-MCP2 — MCP retrieve tool input/output alignment

> Source: 2026-06-01 review session.
> Two improvements to `POST /mcp/v1` `retrieve` tool:
> (1) `inputSchema` hardening — `additionalProperties:false` + richer field descriptions so MCP hosts and agents have an accurate closed schema.
> (2) Response text aligned with `_render_context()` convention — `[資料來源 #N]` + `---` format with metadata header so calling agents can cite chunks without a second `json.loads`.

| # | Category | Task | Status | Owner |
|---|---|---|:---:|---|
| T-MCP2.1 | Behavioral | • **Achieve:** `inputSchema` is a closed schema — unknown arguments are rejected with -32602.<br>• **Deliver:** `tests/unit/test_mcp_tools_call_retrieve.py::test_tools_call_retrieve_rejects_unknown_argument` — extra field → -32602 `MCP_TOOL_INPUT_INVALID`. Add `additionalProperties:false` + improve field descriptions. | [x] | Dev |
| T-MCP2.2 | Behavioral | • **Achieve:** `tools/call retrieve` response `content[0].text` is `[資料來源 #N]`-formatted text, not a JSON blob.<br>• **Deliver:** `tests/unit/test_mcp_tools_call_retrieve.py::test_tools_call_retrieve_text_format_*` (numbered sources, metadata header, empty result, excerpt truncation). Update existing JSON-parse tests to match new format. | [x] | Dev |

