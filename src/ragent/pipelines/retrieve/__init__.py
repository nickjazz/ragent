"""T3.6 — Chat retrieval pipeline factory: QueryEmbed→ES{Vector+BM25}→Join→Hydrate (C6, B26)."""

from __future__ import annotations

from typing import Any

import anyio.from_thread  # noqa: F401 — test seam: test_distributed_seam_logs patches retrieve_mod.anyio.from_thread.run
import structlog
from haystack.components.joiners import DocumentJoiner
from haystack.core.pipeline import Pipeline
from haystack.dataclasses import Document
from haystack_integrations.components.retrievers.elasticsearch import (
    ElasticsearchBM25Retriever,
    ElasticsearchEmbeddingRetriever,
)

from ragent.pipelines.observability import wrap_pipeline_component
from ragent.pipelines.retrieve._constants import (
    _VALID_MODES,
    EXCERPT_MAX_CHARS_DEFAULT,
    MAX_TOP_K,
)
from ragent.pipelines.retrieve.hydrator import (
    _ExcerptTruncator,
    _LLMGenerator,
    _Reranker,
    _SourceHydrator,
)
from ragent.pipelines.retrieve.joiner import (
    _HAYSTACK_JOIN_MODE,
    build_document_id_filter,
    build_es_filters,
    dedupe_by_document,
    doc_to_source_entry,
)
from ragent.pipelines.retrieve.query_embedder import (
    _ES_EMBEDDING_FIELD,
    _DynamicFieldEmbeddingRetriever,
    _QueryEmbedder,
)
from ragent.pipelines.retrieve.retriever import (
    _FEEDBACK_CHUNK_FAN_OUT,
    _FEEDBACK_KNN_NUM_CANDIDATES,
    _FEEDBACK_KNN_SIZE,
    _VOTE_DISLIKE,
    _VOTE_LIKE,
    _FeedbackMemoryRetriever,
)
from ragent.utility.env import int_env, optional_float_env

# Re-read env at __init__ import time so that the boot-time guard re-fires
# when this module is reloaded (e.g. in tests).  _constants.py is cached
# after first import so its assignments are only evaluated once; the guard
# lives here to catch operator misconfiguration on every fresh import of
# ragent.pipelines.retrieve.
DEFAULT_TOP_K = int_env("RETRIEVAL_TOP_K", 20)
if not 1 <= DEFAULT_TOP_K <= MAX_TOP_K:
    raise RuntimeError(
        f"RETRIEVAL_TOP_K={DEFAULT_TOP_K} is outside the advertised [1, {MAX_TOP_K}] "
        f"contract (spec §3.4.4 / §3.8.3). MCP clients calling with omitted top_k "
        f"would bypass the schema maximum."
    )
DEFAULT_MIN_SCORE: float | None = optional_float_env("RETRIEVAL_MIN_SCORE")
if DEFAULT_MIN_SCORE is not None and DEFAULT_MIN_SCORE < 0.0:
    raise RuntimeError(
        f"RETRIEVAL_MIN_SCORE={DEFAULT_MIN_SCORE} must be >= 0.0 — "
        f"score thresholds cannot be negative."
    )

_logger = structlog.get_logger(__name__)

__all__ = [
    # constants (re-exported from _constants)
    "EXCERPT_MAX_CHARS_DEFAULT",
    "MAX_TOP_K",
    "DEFAULT_TOP_K",
    "DEFAULT_MIN_SCORE",
    "_VALID_MODES",
    # joiner
    "build_document_id_filter",
    "build_es_filters",
    "dedupe_by_document",
    "doc_to_source_entry",
    "_HAYSTACK_JOIN_MODE",
    # query_embedder
    "_QueryEmbedder",
    "_DynamicFieldEmbeddingRetriever",
    "_ES_EMBEDDING_FIELD",
    # hydrator
    "_SourceHydrator",
    "_Reranker",
    "_LLMGenerator",
    "_ExcerptTruncator",
    # retriever
    "_FeedbackMemoryRetriever",
    "_FEEDBACK_KNN_SIZE",
    "_FEEDBACK_KNN_NUM_CANDIDATES",
    "_FEEDBACK_CHUNK_FAN_OUT",
    "_VOTE_LIKE",
    "_VOTE_DISLIKE",
    # factory / runner (defined here)
    "build_retrieval_pipeline",
    "run_retrieval",
]


def build_retrieval_pipeline(
    embedder: Any = None,
    document_store: Any = None,
    doc_repo: Any = None,
    join_mode: str = "rrf",
    top_k: int = DEFAULT_TOP_K,
    rerank_client: Any | None = None,
    *,
    registry: Any = None,
    embed_query_callable: Any = None,
    feedback_retriever: Any | None = None,
    feedback_weight: float = 0.5,
    excerpt_max_chars: int = EXCERPT_MAX_CHARS_DEFAULT,
) -> Pipeline:
    """Build the retrieval pipeline.

    Two modes (B50 rollout):

    - **Legacy**: pass ``embedder`` (single ``EmbeddingClient``). The query
      path embeds with it and runs kNN against the hardcoded ``embedding``
      ES field. Kept for pre-T-EM tests and back-compat.
    - **Registry**: pass ``registry`` + ``embed_query_callable``. The query
      path reads ``registry.read_model()`` per request, embeds with that
      model, and runs kNN against ``embedding_<m>_<d>`` — picking up
      cutover/rollback without an App restart.
    """
    if join_mode not in _VALID_MODES:
        raise ValueError(f"join_mode must be one of {sorted(_VALID_MODES)}, got {join_mode!r}")

    use_registry = registry is not None and embed_query_callable is not None
    # Feedback retriever joins as a 3rd RRF input only in rrf join mode (B54).
    use_feedback = feedback_retriever is not None and join_mode == "rrf"

    pipeline = Pipeline()

    def _add(name: str, component: Any) -> None:
        pipeline.add_component(
            name, wrap_pipeline_component(component, namespace="retrieve", step=name)
        )

    _add("source_hydrator", _SourceHydrator(doc_repo))
    _add("excerpt_truncator", _ExcerptTruncator(max_chars=excerpt_max_chars))
    pipeline.connect("source_hydrator.documents", "excerpt_truncator.documents")

    # The retriever output feeds either reranker → source_hydrator (when a
    # rerank_client is configured) or source_hydrator directly.
    if rerank_client is not None:
        _add("reranker", _Reranker(rerank_client, top_k=top_k))
        pipeline.connect("reranker.documents", "source_hydrator.documents")
        retriever_sink = "reranker.documents"
    else:
        retriever_sink = "source_hydrator.documents"

    def _build_query_embedder() -> _QueryEmbedder:
        return (
            _QueryEmbedder(registry=registry, embed_callable=embed_query_callable)
            if use_registry
            else _QueryEmbedder(embedder)
        )

    def _build_vector_retriever() -> Any:
        if use_registry:
            return _DynamicFieldEmbeddingRetriever(document_store=document_store, top_k=top_k)
        return ElasticsearchEmbeddingRetriever(document_store=document_store, top_k=top_k)

    def _connect_query_to_retriever() -> None:
        pipeline.connect("query_embedder.query_embedding", "vector_retriever.query_embedding")
        if use_registry:
            pipeline.connect("query_embedder.embedding_field", "vector_retriever.embedding_field")

    if join_mode == "vector_only":
        _add("query_embedder", _build_query_embedder())
        _add("vector_retriever", _build_vector_retriever())
        _connect_query_to_retriever()
        pipeline.connect("vector_retriever.documents", retriever_sink)

    elif join_mode == "bm25_only":
        _add(
            "bm25_retriever",
            ElasticsearchBM25Retriever(document_store=document_store, top_k=top_k),
        )
        pipeline.connect("bm25_retriever.documents", retriever_sink)

    else:  # rrf or concatenate
        _add("query_embedder", _build_query_embedder())
        _add("vector_retriever", _build_vector_retriever())
        _add(
            "bm25_retriever",
            ElasticsearchBM25Retriever(document_store=document_store, top_k=top_k),
        )
        if use_feedback:
            weights = [1.0, 1.0, feedback_weight]
            _add("feedback_retriever", feedback_retriever)
            _add(
                "joiner",
                DocumentJoiner(
                    join_mode=_HAYSTACK_JOIN_MODE[join_mode], top_k=top_k, weights=weights
                ),
            )
        else:
            _add("joiner", DocumentJoiner(join_mode=_HAYSTACK_JOIN_MODE[join_mode], top_k=top_k))
        _connect_query_to_retriever()
        # Connection order is the joiner's positional input order. `weights`
        # is matched positionally, so feedback MUST be connected LAST to
        # receive the `feedback_weight` slot (PR #80 review, gemini high).
        pipeline.connect("vector_retriever.documents", "joiner.documents")
        pipeline.connect("bm25_retriever.documents", "joiner.documents")
        if use_feedback:
            pipeline.connect("query_embedder.query_embedding", "feedback_retriever.query_embedding")
            pipeline.connect("feedback_retriever.documents", "joiner.documents")
        pipeline.connect("joiner.documents", retriever_sink)

    return pipeline


def _retriever_params(
    filters: dict | None,
    top_k: int | None,
) -> dict[str, Any]:
    """Build shared optional params for ES retriever components."""
    p: dict[str, Any] = {}
    if filters:
        p["filters"] = filters
    if top_k is not None:
        p["top_k"] = top_k
    return p


def _scope_from_haystack_filters(filters: dict | None) -> dict[str, str] | None:
    """Flatten Haystack composite filters to ``{source_app, source_meta}``.

    ``build_es_filters`` returns Haystack's ES integration shape (composite
    `{operator, conditions: [{field, operator, value}, …]}` or leaf
    `{field, operator, value}`), but ``_FeedbackMemoryRetriever`` hand-rolls
    its kNN body and reads flat keys. Without this bridge, app/meta-scoped
    chat would silently bypass the feedback retriever's scope filter
    (PR #80 review, codex P1).
    """
    if not filters:
        return None
    flat: dict[str, str] = {}

    def _walk(node: dict) -> None:
        if "field" in node:
            flat[node["field"]] = node["value"]
        elif "conditions" in node:
            for child in node["conditions"]:
                _walk(child)

    _walk(filters)
    return flat or None


def run_retrieval(
    pipeline: Pipeline,
    query: str,
    filters: dict | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[Document]:
    """Run the retrieval pipeline; returns hydrated documents.

    Inspects which components are present and populates only the required inputs.
    """
    nodes = set(pipeline.graph.nodes)
    inputs: dict[str, dict] = {}
    rp = _retriever_params(filters, top_k)

    if "query_embedder" in nodes:
        inputs["query_embedder"] = {"query": query}
    if "bm25_retriever" in nodes:
        inputs["bm25_retriever"] = {"query": query, **rp}
    if "vector_retriever" in nodes and rp:
        inputs["vector_retriever"] = rp
    # Feedback retriever must also see app/meta filters so its kNN over
    # feedback_v1 honours the same scope as the vector/BM25 path. Without
    # this, app-scoped chat could be boosted by liked sources from other
    # apps via the joiner (PR #80 review, codex P1).
    if "feedback_retriever" in nodes:
        scope = _scope_from_haystack_filters(filters)
        fb_inputs: dict[str, Any] = {}
        if scope:
            fb_inputs["filters"] = scope
        if top_k is not None:
            fb_inputs["top_k"] = top_k
        if fb_inputs:
            inputs["feedback_retriever"] = fb_inputs
    if "joiner" in nodes and top_k is not None:
        inputs["joiner"] = {"top_k": top_k}
    if "reranker" in nodes:
        rr_inputs: dict[str, Any] = {"query": query}
        if top_k is not None:
            rr_inputs["top_k"] = top_k
        inputs["reranker"] = rr_inputs

    result = pipeline.run(inputs)
    docs = result.get("excerpt_truncator", {}).get("documents", [])
    if min_score is not None:
        docs = [d for d in docs if d.score is not None and d.score >= min_score]
    if top_k is not None:
        docs = docs[:top_k]
    return docs
