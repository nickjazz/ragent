"""Source hydration, reranking, LLM generation, and excerpt truncation components."""

from __future__ import annotations

import dataclasses
from typing import Any

import anyio.from_thread
import httpx
import structlog
from haystack.core.component import component
from haystack.dataclasses import Document

from ragent.bootstrap.metrics import record_rerank_degraded
from ragent.errors.upstream import UpstreamServiceError, UpstreamTimeoutError
from ragent.pipelines.retrieve._constants import DEFAULT_TOP_K, EXCERPT_MAX_CHARS_DEFAULT

_logger = structlog.get_logger(__name__)


@component
class _SourceHydrator:
    """Enrich READY chunks with source metadata; drop the rest (B36).

    Acts as the retrieval correctness gate: chunks whose ``document_id`` is
    not in the READY rows returned by ``get_sources_by_document_ids`` are
    dropped, so orphan / mid-flight / demoted chunks never reach LLM
    context or ``sources[]`` regardless of cleanup-path completeness.
    """

    def __init__(self, doc_repo: Any) -> None:
        self._repo = doc_repo

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict:
        ids = [d.meta.get("document_id") for d in documents if d.meta.get("document_id")]
        sources = anyio.from_thread.run(self._repo.get_sources_by_document_ids, ids) if ids else {}
        result = []
        for doc in documents:
            doc_id = doc.meta.get("document_id")
            if not (doc_id and doc_id in sources):
                continue
            source_app, source_id, source_title = sources[doc_id]
            meta = {
                **doc.meta,
                "source_app": source_app,
                "source_id": source_id,
                "source_title": source_title,
            }
            result.append(dataclasses.replace(doc, meta=meta))
        before = len(documents)
        after = len(result)
        if after < before:
            _logger.info(
                "chat.hydrator.dropped",
                before_count=before,
                after_count=after,
                dropped_count=before - after,
            )
        return {"documents": result}


@component
class _Reranker:
    """Wrap RerankClient as a Haystack component.

    Sits between the joiner (or single retriever) and source_hydrator so
    rerank scoring sees full chunk content, before excerpt truncation.
    """

    def __init__(self, rerank_client: Any, top_k: int = DEFAULT_TOP_K) -> None:
        self._client = rerank_client
        self._top_k = top_k

    @component.output_types(documents=list[Document])
    def run(self, query: str, documents: list[Document], top_k: int | None = None) -> dict:
        if not documents:
            return {"documents": []}
        k = top_k if top_k is not None else self._top_k
        texts = [d.content or "" for d in documents]
        try:
            results = self._client.rerank(query=query, texts=texts, top_k=k)
        except UpstreamServiceError as exc:
            # 4xx responses (auth failures, bad-request config errors) are NOT
            # transient — re-raise so they surface as hard errors instead of
            # silently degrading ranking. Only 5xx / timeout warrant fail-open.
            cause = exc.__cause__
            if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code < 500:
                raise
            # UpstreamTimeoutError is a subclass of UpstreamServiceError; check
            # it first so the reason label discriminates timeout from 5xx errors.
            reason = "timeout" if isinstance(exc, UpstreamTimeoutError) else "5xx"
            _logger.warning("rerank.degraded", reason=reason, candidate_count=len(documents))
            record_rerank_degraded(reason)
            return {"documents": documents[:k]}
        ordered: list[Document] = []
        invalid = 0
        for r in results[:k]:
            i = r.get("index")
            # bool is an int subclass, so isinstance(True, int) is True; reject
            # explicitly so {"index": True} is not silently treated as docs[1].
            if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(documents):
                invalid += 1
                continue
            score = r.get("score")
            doc = documents[i]
            ordered.append(dataclasses.replace(doc, score=score) if score is not None else doc)
        if invalid:
            # Reranker returned indices outside the candidate set — surfaces
            # contract drift between retrieval top_k and rerank result_count.
            _logger.warning(
                "rerank.invalid_indices",
                invalid_count=invalid,
                result_count=len(results),
                document_count=len(documents),
            )
        return {"documents": ordered}


@component
class _LLMGenerator:
    """Wrap LLMClient.chat as a Haystack component.

    Terminal node for non-streaming chat: takes RAG-built messages plus
    cited documents, returns the answer string and passes documents
    through for citation rendering.
    """

    def __init__(self, llm_client: Any) -> None:
        self._client = llm_client

    @component.output_types(answer=str, documents=list[Document], usage=dict)
    def run(
        self,
        messages: list[dict],
        documents: list[Document],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        result = self._client.chat(
            messages=messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
        return {"answer": result["content"], "documents": documents, "usage": result["usage"]}


@component
class _ExcerptTruncator:
    """Truncate chunk content to EXCERPT_MAX_CHARS for response payloads."""

    def __init__(self, max_chars: int = EXCERPT_MAX_CHARS_DEFAULT) -> None:
        self._max = max_chars

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict:
        result = []
        for doc in documents:
            # Display layer prefers raw byte slice; fall back to normalized
            # content for legacy chunks predating raw_content.
            source = (doc.meta or {}).get("raw_content") or (doc.content or "")
            truncated = source[: self._max]
            if truncated != doc.content:
                result.append(dataclasses.replace(doc, content=truncated))
            else:
                result.append(doc)
        return {"documents": result}
