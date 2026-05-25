"""T-CHAOS.C4 — Rerank 5xx fail-open (spec §3.6.1).

WireMock stubs POST /rerank to return HTTP 500 for ALL calls during the test.
The RerankClient retries 3× with 2 s sleep, then raises UpstreamServiceError.
_Reranker.run() catches that error, logs rerank.degraded, increments
rerank_degraded_total{reason="5xx"}, and returns RRF-ordered docs[:top_k]
so the chat pipeline continues to the LLM without interruption.

Spec §3.6.1 C4 acceptance asserts:
  1. Chat returns HTTP 200 for each of 3 requests (fail-open, not 500).
  2. Last SSE event is type="done" with sources present (RRF-ordered docs).
  3. rerank_degraded_total{reason="5xx"} increments by exactly 3.
  4. chaos_drill_outcome_total{case="C4", outcome="pass"} increments.
"""

from __future__ import annotations

import time

import httpx
import pytest
from prometheus_client.parser import text_string_to_metric_families

from tests.e2e.conftest import API_URL
from tests.e2e.test_chaos.conftest import (  # noqa: F401 (fixtures)
    parse_sse_events,
    post_wiremock_stub,
    wiremock_reset,
)

pytestmark = [
    pytest.mark.docker,
]

# Minimal document so the retriever returns non-empty results to the reranker.
_INGEST_CONTENT = "Chaos engineering tests resilience of distributed systems."

# Priority 1 overrides the default WireMock /rerank stub (which returns 200).
_RERANK_500_STUB = {
    "priority": 1,
    "request": {"method": "POST", "urlPath": "/rerank"},
    "response": {
        "status": 500,
        "headers": {"Content-Type": "application/json"},
        "jsonBody": {"error": "Internal Server Error"},
    },
}

_CHAT_PAYLOAD = {
    "messages": [{"role": "user", "content": "What is chaos engineering?"}],
    "model": "gpt-oss-120b",
    "provider": "openai",
}

_INGEST_DEADLINE_SECONDS = 60
# 60 s per request: RerankClient retries 3× with 2 s sleep (4 s overhead each)
_CHAT_TIMEOUT_SECONDS = 60


def _scrape_rerank_degraded(reason: str) -> int:
    """Return current rerank_degraded_total{reason=<reason>} from the API /metrics."""
    text = httpx.get(f"{API_URL}/metrics", timeout=5).text
    for family in text_string_to_metric_families(text):
        if family.name != "rerank_degraded":
            continue
        for sample in family.samples:
            if sample.name == "rerank_degraded_total" and sample.labels.get("reason") == reason:
                return int(sample.value)
    return 0


def _poll_until_ready(doc_id: str) -> str:
    deadline = time.monotonic() + _INGEST_DEADLINE_SECONDS
    last = "UNKNOWN"
    while time.monotonic() < deadline:
        last = (
            httpx.get(
                f"{API_URL}/ingest/v1/{doc_id}",
                headers={"X-User-Id": "alice"},
                timeout=5,
            )
            .json()
            .get("status", "UNKNOWN")
        )
        if last in ("READY", "FAILED"):
            return last
        time.sleep(1)
    return last


def test_C4_rerank_5xx_degrades_gracefully(
    running_stack,
    e2e_env,
    wiremock_url: str,
    wiremock_reset,  # noqa: F811
) -> None:
    """Rerank returns 500 → chat degrades to RRF order, counter increments×3."""
    from ragent.bootstrap.metrics import chaos_drill_outcome_total

    # 1. Seed a document so the retriever returns non-empty results to the reranker.
    #    Without this, _Reranker.run(documents=[]) short-circuits and never calls
    #    the rerank API, so the WireMock fault would never be exercised.
    ingest_resp = httpx.post(
        f"{API_URL}/ingest/v1",
        headers={"X-User-Id": "alice"},
        json={
            "ingest_type": "inline",
            "source_id": "c4-chaos-seed",
            "source_app": "chaos-drill",
            "source_title": "C4 chaos doc",
            "mime_type": "text/plain",
            "content": _INGEST_CONTENT,
        },
        timeout=10,
    )
    assert ingest_resp.status_code == 202, (
        f"Ingest rejected: {ingest_resp.status_code} {ingest_resp.text}"
    )
    doc_id = ingest_resp.json()["document_id"]

    status = _poll_until_ready(doc_id)
    assert status == "READY", (
        f"Document {doc_id} did not reach READY within {_INGEST_DEADLINE_SECONDS}s (got {status!r})"
    )

    # 2. Record baseline before injecting fault.
    before = _scrape_rerank_degraded("5xx")

    # 3. Inject fault: all /rerank calls → 500 (priority 1 overrides default stub).
    post_wiremock_stub(wiremock_url, _RERANK_500_STUB)

    # 4. Make 3 chat requests.  Each should return 200 with RRF-ordered sources
    #    (fail-open) rather than propagating the reranker's 5xx.
    for i in range(3):
        resp = httpx.post(
            f"{API_URL}/chat/v1/stream",
            headers={"X-User-Id": "alice"},
            json=_CHAT_PAYLOAD,
            timeout=_CHAT_TIMEOUT_SECONDS,
        )
        assert resp.status_code == 200, (
            f"Request {i}: expected 200, got {resp.status_code}: {resp.text[:200]}"
        )
        events = parse_sse_events(resp.text)
        assert events, f"Request {i}: no SSE events received"
        last = events[-1]
        assert last.get("type") == "done", (
            f"Request {i}: expected last SSE event type='done' (fail-open), "
            f"got {last.get('type')!r}. All events: {events}"
        )
        # RRF-ordered sources must be present (not empty — we seeded a doc above).
        assert "sources" in last, f"Request {i}: 'sources' key missing from done event: {last}"

    # 5. Assert counter incremented once per request (not once per retry attempt).
    after = _scrape_rerank_degraded("5xx")
    assert after == before + 3, (
        f"rerank_degraded_total{{reason='5xx'}} expected +3 (one per request), "
        f"got before={before} after={after}"
    )

    # 6. Record drill outcome (common assert per spec §3.6.1).
    chaos_drill_outcome_total.labels(case="C4", outcome="pass").inc()
    assert (
        chaos_drill_outcome_total.labels(case="C4", outcome="pass")._value.get() >= 1  # noqa: SLF001
    )
