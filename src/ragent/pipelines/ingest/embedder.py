"""DocumentEmbedder — multi-model dual-write (B50 T-EM.15)."""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog
from haystack.core.component import component
from haystack.dataclasses import Document

_logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# DocumentEmbedder — multi-model dual-write (B50 T-EM.15)
# ---------------------------------------------------------------------------


@component
class DocumentEmbedder:
    """Wraps the project's external EmbeddingClient as a Haystack component.

    Two construction modes:

    - **Legacy single-model**: ``DocumentEmbedder(client)`` — embeds every
      chunk with the given client and stores the vector on ``doc.embedding``.
      Kept for tests and the pre-T-EM ingest path.

    - **Registry mode (B50 T-EM-R.6)**: ``DocumentEmbedder(registry, embed_callable, es_client)``
      — reads ``registry.write_models()`` on every ``run()``; bulk-writes to
      ``registry.stable_index`` (and ``registry.candidate_index`` during
      CANDIDATE/CUTOVER) using field ``"embedding"`` in each index. Returns
      ``{"documents": []}`` — the embedder is the sole ES writer, no downstream
      DocumentWriter needed. Empty ``write_models()`` raises ``RuntimeError``.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        registry: Any = None,
        embed_callable: Any = None,
        es_client: Any = None,
    ) -> None:
        if registry is not None:
            if embed_callable is None:
                raise ValueError("registry mode requires embed_callable")
            if es_client is None:
                raise ValueError("registry mode requires es_client")
            self._mode = "dual"
            self._registry = registry
            self._embed = embed_callable
            self._es = es_client
            self._client = None
        else:
            self._mode = "legacy"
            self._client = client
            self._registry = None
            self._embed = None
            self._es = None

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict:
        if not documents:
            return {"documents": []}
        if self._mode == "legacy":
            return self._run_legacy(documents)
        return self._run_dual(documents)

    def _run_legacy(self, documents: list[Document]) -> dict:
        texts = [d.content or "" for d in documents]
        embeddings = self._client.embed(texts)
        out = [
            dataclasses.replace(d, embedding=e) for d, e in zip(documents, embeddings, strict=True)
        ]
        return {"documents": out}

    def _run_dual(self, documents: list[Document]) -> dict:
        models = list(self._registry.write_models())
        if not models:
            raise RuntimeError(
                "ActiveModelRegistry returned no write_models — refusing to emit unindexed chunks"
            )
        texts = [d.content or "" for d in documents]

        if len(models) == 1:
            results = [self._embed(models[0], texts)]
        else:
            with ThreadPoolExecutor(max_workers=len(models)) as pool:
                results = list(pool.map(lambda m: self._embed(m, texts), models))

        index_names = [self._registry.stable_index]
        if len(models) > 1:
            candidate_idx = self._registry.candidate_index
            if candidate_idx is None:
                raise RuntimeError("write_models() returned 2 models but candidate_index is None")
            index_names.append(candidate_idx)

        for vectors, index_name in zip(results, index_names, strict=True):
            ops: list[dict] = []
            op_docs: list[tuple[Document, list[float]]] = []
            for doc, vec in zip(documents, vectors, strict=True):
                ops.append({"index": {"_id": doc.id}})
                # meta is written first so that "embedding" and "content" always win.
                body: dict = {**(doc.meta or {}), "embedding": vec}
                if doc.content is not None:
                    body["content"] = doc.content
                ops.append(body)
                op_docs.append((doc, vec))
            response = self._es.bulk(index=index_name, operations=ops)
            self._handle_bulk_response(response, op_docs, index_name)

        return {"documents": []}

    def _handle_bulk_response(
        self,
        response: dict,
        op_docs: list[tuple[Document, list[float]]],
        index_name: str,
    ) -> None:
        """Check bulk response for partial failures and retry failed items."""
        if not response.get("errors"):
            return

        items = response.get("items", [])
        retry_op_docs: list[tuple[Document, list[float]]] = []
        for item, (doc, vec) in zip(items, op_docs, strict=True):
            action = item.get("index", {})
            status = action.get("status", 200)
            if status >= 400:
                _logger.warning(
                    "es.bulk_partial_failure",
                    index=index_name,
                    doc_id=action.get("_id"),
                    status=status,
                    error=action.get("error"),
                )
                retry_op_docs.append((doc, vec))

        if not retry_op_docs:
            return

        retry_ops: list[dict] = []
        for doc, vec in retry_op_docs:
            retry_ops.append({"index": {"_id": doc.id}})
            body: dict = {**(doc.meta or {}), "embedding": vec}
            if doc.content is not None:
                body["content"] = doc.content
            retry_ops.append(body)
        retry_response = self._es.bulk(index=index_name, operations=retry_ops)
        if retry_response.get("errors"):
            _logger.error(
                "es.bulk_retry_partial_failure",
                index=index_name,
                failed_ids=[
                    item.get("index", {}).get("_id")
                    for item in retry_response.get("items", [])
                    if item.get("index", {}).get("status", 200) >= 400
                ],
            )
