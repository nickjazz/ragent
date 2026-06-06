"""Query embedder components for kNN retrieval."""

from __future__ import annotations

from typing import Any

from haystack.core.component import component
from haystack.dataclasses import Document
from haystack_integrations.document_stores.elasticsearch.filters import _normalize_filters

from ragent.pipelines.retrieve._constants import DEFAULT_TOP_K

# Every ES index in the index-per-model lifecycle (chunks_v1, chunks_v2, …)
# uses this single dense_vector field name.  Lifecycle cutover flips the read
# alias, not the field name (B61).
_ES_EMBEDDING_FIELD = "embedding"


@component
class _QueryEmbedder:
    """Embed the query for kNN retrieval.

    Two construction modes (back-compat during the B50 rollout):

    - **Legacy single-model**: ``_QueryEmbedder(embedder)`` calls
      ``embedder.embed([q], query=True)`` and emits ``{query, query_embedding}``.
      Kept for the existing pre-T-EM integration tests.

    - **Registry mode**: ``_QueryEmbedder(registry=, embed_callable=)`` calls
      ``registry.read_model()`` per request, embeds with that one model via
      ``embed_callable(model, texts)``, and always emits
      ``embedding_field="embedding"``.  Every index in the index-per-model
      lifecycle (``chunks_v1``, ``chunks_v2``, …) uses the same
      ``"embedding"`` dense_vector field name; lifecycle cutover works by
      flipping the read alias (``chunks_v1_active``), not by switching field
      names.  The fresh ``read_model()`` lookup on every run is intentional
      — a cutover (alias flip) takes effect on the next query without
      restarting the App.
    """

    def __init__(
        self,
        embedder: Any = None,
        *,
        registry: Any = None,
        embed_callable: Any = None,
    ) -> None:
        if registry is not None:
            if embed_callable is None:
                raise ValueError("registry mode requires embed_callable")
            self._mode = "registry"
            self._registry = registry
            self._embed = embed_callable
            self._embedder = None
        else:
            self._mode = "legacy"
            self._embedder = embedder
            self._registry = None
            self._embed = None

    @component.output_types(query=str, query_embedding=list[float], embedding_field=str)
    def run(self, query: str) -> dict:
        if self._mode == "legacy":
            embedding = self._embedder.embed([query], query=True)[0]
            return {"query": query, "query_embedding": embedding}
        model = self._registry.read_model()
        embedding = self._embed(model, [query])[0]
        return {
            "query": query,
            "query_embedding": embedding,
            "embedding_field": _ES_EMBEDDING_FIELD,
        }


@component
class _DynamicFieldEmbeddingRetriever:
    """ES kNN retriever that targets a runtime-provided dense_vector field.

    Replaces ``ElasticsearchEmbeddingRetriever`` when the embedding field
    name is determined per-query by the ``ActiveModelRegistry`` (B50).
    Without it, the upstream `_QueryEmbedder`'s `embedding_field` output
    would have no consumer and the kNN query would fall back to
    ``_ES_EMBEDDING_FIELD`` (``"embedding"``).

    Reaches into ``document_store._search_documents(**body)`` to bypass the
    haystack-elasticsearch retriever's hardcoded ``"field": "embedding"``.
    The store's public API does not expose a per-call field override yet.
    Because that path skips the store's own filter normalisation (which
    ``_bm25_retrieval`` / ``_embedding_retrieval`` do), filters must be
    normalised to ES query DSL here before reaching the client.
    """

    def __init__(
        self,
        document_store: Any,
        top_k: int = DEFAULT_TOP_K,
        num_candidates: int | None = None,
    ) -> None:
        self._store = document_store
        self._top_k = top_k
        self._num_candidates = num_candidates

    @component.output_types(documents=list[Document])
    def run(
        self,
        query_embedding: list[float],
        embedding_field: str = _ES_EMBEDDING_FIELD,
        filters: dict | None = None,
        top_k: int | None = None,
    ) -> dict:
        if not query_embedding:
            raise ValueError("query_embedding must be a non-empty list of floats")
        k = top_k if top_k is not None else self._top_k
        num_candidates = self._num_candidates or k * 10
        body: dict[str, Any] = {
            "knn": {
                "field": embedding_field,
                "query_vector": query_embedding,
                "k": k,
                "num_candidates": num_candidates,
            },
        }
        if filters:
            body["knn"]["filter"] = _normalize_filters(filters)
        docs = self._store._search_documents(**body)
        return {"documents": docs}
