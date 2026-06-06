"""Feedback memory retriever component."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import anyio.from_thread
import structlog
from haystack.core.component import component
from haystack.dataclasses import Document

from ragent.pipelines.retrieve._constants import DEFAULT_TOP_K
from ragent.utility.datetime import utcnow
from ragent.utility.wilson import wilson_lower_bound

_logger = structlog.get_logger(__name__)

# Feedback retriever ES knobs — sized for the dedup-by-source flow:
# kNN returns up to FEEDBACK_KNN_SIZE raw hits, which collapse into ≤ top_k
# distinct (source_app, source_id) buckets after aggregation. num_candidates
# is the ES HNSW recall budget per shard.
_FEEDBACK_KNN_SIZE = 100
_FEEDBACK_KNN_NUM_CANDIDATES = 400
# Fan-out per source on the chunks_v1 lookup — most documents have ≤ 10 chunks
# in the active READY revision, so top_k*10 caps the post-aggregation page.
_FEEDBACK_CHUNK_FAN_OUT = 10
_VOTE_LIKE = 1
_VOTE_DISLIKE = -1


@component
class _FeedbackMemoryRetriever:
    """3rd RRF input: boost sources liked on semantically-similar past queries (B54).

    Pipeline within ``run()``:
      1. ES kNN on ``feedback_v1.query_embedding`` (filter: ``ts > now-90d``,
         optional source_app/source_meta, score floor for cosine ≥ 0.7).
      2. Aggregate per (source_app, source_id) → (likes, dislikes, ts_max).
      3. Gate ``(likes+dislikes) ≥ min_votes``.
      4. score = wilson_lb(likes, total) × 0.5 ** (days_since_ts_max / half_life_days).
      5. DocumentRepository.get_document_ids_by_source(pairs) → current READY docs.
      6. Single ES `terms` query on ``chunks_v1.document_id`` (NOT N+1).
      7. Each chunk inherits the parent source's Wilson-decayed score.

    Both ES calls pass ``request_timeout=ES_QUERY_TIMEOUT_SECONDS`` so the
    feedback path shares the existing chat-retrieval budget.
    """

    # Score floor: ES cosine similarity maps cos∈[-1,1] → score=(1+cos)/2 ∈ [0,1].
    # cosine_threshold=0.7 ⇒ min_score = (1 + 0.7) / 2 = 0.85.
    def __init__(
        self,
        es_client: Any,
        doc_repo: Any,
        *,
        feedback_index: str = "feedback_v1",
        chunks_index: str = "chunks_v1",
        top_k: int = DEFAULT_TOP_K,
        min_votes: int = 3,
        half_life_days: int = 14,
        cosine_threshold: float = 0.7,
        lookback_days: int = 90,
        request_timeout: float | None = None,
    ) -> None:
        self._es = es_client
        self._repo = doc_repo
        self._feedback_index = feedback_index
        self._chunks_index = chunks_index
        self._top_k = top_k
        self._min_votes = min_votes
        self._half_life_days = half_life_days
        self._min_score = (1.0 + cosine_threshold) / 2.0
        self._lookback_days = lookback_days
        # Default matches ES_QUERY_TIMEOUT_SECONDS at the call site in composition.py.
        self._request_timeout = request_timeout

    def _search_kwargs(self) -> dict:
        return (
            {"request_timeout": self._request_timeout} if self._request_timeout is not None else {}
        )

    @component.output_types(documents=list[Document])
    def run(
        self,
        query_embedding: list[float],
        filters: dict | None = None,
        top_k: int | None = None,
    ) -> dict:
        k = top_k if top_k is not None else self._top_k
        # 1. kNN feedback_v1
        knn_filter: list[dict] = [{"range": {"ts": {"gte": f"now-{self._lookback_days}d"}}}]
        if filters:
            sa = filters.get("source_app")
            sm = filters.get("source_meta")
            if sa:
                knn_filter.append({"term": {"source_app": sa}})
            if sm:
                knn_filter.append({"term": {"source_meta": sm}})
        knn_body = {
            "size": _FEEDBACK_KNN_SIZE,
            "min_score": self._min_score,
            "knn": {
                "field": "query_embedding",
                "query_vector": query_embedding,
                "k": _FEEDBACK_KNN_SIZE,
                "num_candidates": _FEEDBACK_KNN_NUM_CANDIDATES,
                "filter": {"bool": {"filter": knn_filter}},
            },
            "_source": ["source_app", "source_id", "vote", "ts"],
        }
        hits = self._es.search(index=self._feedback_index, body=knn_body, **self._search_kwargs())[
            "hits"
        ]["hits"]
        if not hits:
            # Cold-start / unrelated query — operators want to see the rate
            # so they know whether feedback boost ever fires.  No content,
            # only the bool filter shape and zero count (no embedding logged).
            _logger.debug(
                "feedback.retriever.empty_knn",
                source_app_filter=bool(filters and filters.get("source_app")),
                source_meta_filter=bool(filters and filters.get("source_meta")),
            )
            return {"documents": []}

        # 2. Aggregate per (source_app, source_id)
        agg: dict[tuple[str, str], dict] = {}
        for h in hits:
            src = h["_source"]
            key = (src["source_app"], src["source_id"])
            bucket = agg.setdefault(key, {"likes": 0, "dislikes": 0, "ts_max": None})
            if src["vote"] == _VOTE_LIKE:
                bucket["likes"] += 1
            elif src["vote"] == _VOTE_DISLIKE:
                bucket["dislikes"] += 1
            ts = src["ts"]
            # ISO 8601 with fixed UTC offset is lexicographically monotonic, so
            # string > comparison is correct without parsing every hit.
            if bucket["ts_max"] is None or ts > bucket["ts_max"]:
                bucket["ts_max"] = ts

        # 3-4. Gate + score
        now = utcnow()
        scored: list[tuple[tuple[str, str], float]] = []
        for key, b in agg.items():
            total = b["likes"] + b["dislikes"]
            if total < self._min_votes:
                continue
            wilson = wilson_lower_bound(b["likes"], total)
            # Python 3.11+ fromisoformat handles trailing Z natively.
            ts_max = datetime.fromisoformat(b["ts_max"])
            age_days = max(0.0, (now - ts_max).total_seconds() / 86400.0)
            decay = 0.5 ** (age_days / self._half_life_days)
            scored.append((key, wilson * decay))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        scored = scored[:k]
        if not scored:
            _logger.info(
                "feedback.retriever.below_min_votes",
                hit_count=len(hits),
                source_count=len(agg),
                min_votes=self._min_votes,
            )
            return {"documents": []}

        # 5. MariaDB: pair → current READY document_id
        pairs = [k for k, _ in scored]
        doc_id_map = anyio.from_thread.run(self._repo.get_document_ids_by_source, pairs)
        scored_by_doc = {doc_id_map[k]: s for k, s in scored if k in doc_id_map}
        if not scored_by_doc:
            # All scored sources lacked a READY row (B36 alignment): possible
            # after recent supersede or full DELETE. Surface for ops.
            _logger.info(
                "feedback.retriever.hydration_miss",
                scored_count=len(scored),
                resolved_count=0,
            )
            return {"documents": []}

        # 6. Single ES terms query on chunks_v1
        terms_body = {
            "size": k * _FEEDBACK_CHUNK_FAN_OUT,
            "query": {"terms": {"document_id": list(scored_by_doc.keys())}},
        }
        chunk_hits = self._es.search(
            index=self._chunks_index, body=terms_body, **self._search_kwargs()
        )["hits"]["hits"]

        # 7. Build Document list, inherit parent score
        result: list[Document] = []
        for ch in chunk_hits:
            src = ch["_source"]
            doc_id = src.get("document_id")
            if doc_id not in scored_by_doc:
                continue
            result.append(
                Document(
                    content=src.get("text") or "",
                    meta={
                        "document_id": doc_id,
                        "chunk_id": src.get("chunk_id"),
                        "source_app": src.get("source_app"),
                        "source_meta": src.get("source_meta"),
                        "source_url": src.get("source_url"),
                        "raw_content": src.get("raw_content"),
                    },
                    score=scored_by_doc[doc_id],
                )
            )
        # Per-request scoring trace — debug (chat QPS could be high). The two
        # short-circuit branches above stay at info because they are rare and
        # operationally meaningful (cold-start, hydration miss).
        _logger.debug(
            "feedback.retriever.scored",
            hit_count=len(hits),
            source_count=len(agg),
            scored_count=len(scored),
            resolved_count=len(scored_by_doc),
            chunk_count=len(result),
        )
        return {"documents": result}
