"""POST /retrieve/v2 — document-scoped retrieval with anti-IDOR gate (spec §3.4.6).

Differences from /retrieve/v1: `document_id_list` is mandatory (never searches
the whole corpus), every id must be owned by the authenticated caller (403
DOCUMENT_FORBIDDEN otherwise, unauthenticated callers included), and the ES
query is hard-scoped to the verified ids via a bool.filter terms clause.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from opentelemetry import trace

from ragent.auth.deps import get_user_id
from ragent.errors.codes import HttpErrorCode
from ragent.errors.problem import problem
from ragent.pipelines.retrieve import (
    EXCERPT_MAX_CHARS_DEFAULT,
    build_document_id_filter,
    doc_to_source_entry,
    run_retrieval,
)
from ragent.schemas.retrieve import ChunkEntry, RetrieveResponse, RetrieveV2Request
from ragent.services.retrieve_v2_service import DocumentForbidden, RetrieveV2Service

logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


def create_retrieve_v2_router(
    retrieval_pipeline: Any,
    retrieve_v2_service: RetrieveV2Service,
    *,
    excerpt_max_chars: int = EXCERPT_MAX_CHARS_DEFAULT,
) -> APIRouter:
    router = APIRouter(prefix="/retrieve/v2")

    @router.post("", response_model=RetrieveResponse)
    async def retrieve_v2(
        body: RetrieveV2Request,
        x_user_id: Annotated[str | None, Depends(get_user_id)] = None,
    ):
        with _tracer.start_as_current_span("retrieve_v2.request") as span:
            if x_user_id:
                span.set_attribute("user_id", x_user_id)
            span.set_attribute("query_len", len(body.query))
            span.set_attribute("document_count", len(body.document_id_list))

            try:
                await retrieve_v2_service.assert_owner(x_user_id, body.document_id_list)
            except DocumentForbidden:
                return problem(
                    403,
                    HttpErrorCode.DOCUMENT_FORBIDDEN,
                    "One or more document ids are not accessible",
                )

            with _tracer.start_as_current_span("retrieve_v2.pipeline") as p_span:
                docs = await run_in_threadpool(
                    run_retrieval,
                    retrieval_pipeline,
                    query=body.query,
                    filters=build_document_id_filter(body.document_id_list),
                    top_k=body.top_k,
                    min_score=body.min_score,
                )
                p_span.set_attribute("result_count", len(docs))
                logger.info(
                    "retrieve_v2.pipeline",
                    query_len=len(body.query),
                    document_count=len(body.document_id_list),
                    result_count=len(docs),
                )
            return RetrieveResponse(
                chunks=[
                    ChunkEntry(**doc_to_source_entry(d, max_chars=excerpt_max_chars)) for d in docs
                ]
            )

    return router
