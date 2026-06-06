"""Operational endpoints for immediate document state management."""

from __future__ import annotations

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ragent.auth.deps import get_user_id
from ragent.services.ingest_service import IngestService

OPS_RETRY_BATCH_LIMIT = 500

_Statuses = list[Literal["UPLOADED", "PENDING", "FAILED"]]


class OpsStatusCount(BaseModel):
    before: int
    after: int


class OpsRetryRequest(BaseModel):
    statuses: _Statuses = Field(min_length=1)
    source_app: str | None = None
    source_id: str | None = None
    created_after: datetime.datetime | None = None
    limit: int = Field(default=OPS_RETRY_BATCH_LIMIT, gt=0, le=OPS_RETRY_BATCH_LIMIT)
    dry_run: bool = False


class OpsRetryResponse(BaseModel):
    dry_run: bool
    counts: dict[str, OpsStatusCount]
    queued: int
    skipped: int


def create_admin_ops_router(svc: IngestService) -> APIRouter:
    router = APIRouter(prefix="/ops/v1")

    @router.post("/retry", status_code=200)
    async def ops_retry(
        body: OpsRetryRequest,
        _user_id: Annotated[str, Depends(get_user_id)],
    ) -> OpsRetryResponse:
        before, after, queued, skipped = await svc.batch_rerun(
            statuses=body.statuses,
            source_app=body.source_app,
            source_id=body.source_id,
            created_after=body.created_after,
            limit=body.limit,
            dry_run=body.dry_run,
        )
        all_statuses = set(before) | set(after)
        counts = {
            s: OpsStatusCount(before=before.get(s, 0), after=after.get(s, 0)) for s in all_statuses
        }
        return OpsRetryResponse(
            dry_run=body.dry_run,
            counts=counts,
            queued=queued,
            skipped=skipped,
        )

    return router
