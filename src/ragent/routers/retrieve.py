"""POST /retrieve standalone retrieval without LLM synthesis."""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from opentelemetry import trace

from ragent.auth.deps import get_user_id
from ragent.pipelines.retrieve import (
    EXCERPT_MAX_CHARS_DEFAULT,
    build_es_filters,
    dedupe_by_document,
    doc_to_source_entry,
    run_retrieval,
)
from ragent.schemas.retrieve import ChunkEntry, RetrieveRequest, RetrieveResponse

logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


def create_retrieve_router(
    retrieval_pipeline: Any,
    *,
    excerpt_max_chars: int = EXCERPT_MAX_CHARS_DEFAULT,
) -> APIRouter:
    router = APIRouter(prefix="/retrieve/v1")

    @router.post("", response_model=RetrieveResponse)
    async def retrieve(
        body: RetrieveRequest,
        x_user_id: Annotated[str | None, Depends(get_user_id)] = None,
    ) -> RetrieveResponse:
        with _tracer.start_as_current_span("retrieve.request") as span:
            if x_user_id:
                span.set_attribute("user_id", x_user_id)
            span.set_attribute("query_len", len(body.query))
            span.set_attribute("dedupe", body.dedupe)
            with _tracer.start_as_current_span("retrieve.pipeline") as p_span:
                docs = await run_in_threadpool(
                    run_retrieval,
                    retrieval_pipeline,
                    query=body.query,
                    filters=build_es_filters(body.source_app, body.source_meta),
                    top_k=body.top_k,
                    min_score=body.min_score,
                )
                p_span.set_attribute("result_count", len(docs))
                logger.info(
                    "retrieve.pipeline",
                    query_len=len(body.query),
                    result_count=len(docs),
                )
            if body.dedupe:
                input_count = len(docs)
                with _tracer.start_as_current_span("retrieve.dedupe") as d_span:
                    docs = dedupe_by_document(docs)
                    d_span.set_attribute("input_count", input_count)
                    d_span.set_attribute("output_count", len(docs))
                    logger.info(
                        "retrieve.dedupe",
                        input_count=input_count,
                        output_count=len(docs),
                    )
            return RetrieveResponse(
                chunks=[
                    ChunkEntry(**doc_to_source_entry(d, max_chars=excerpt_max_chars)) for d in docs
                ]
            )

    return router
