"""Pydantic request models for /embedding/v1 lifecycle endpoints (spec §5, B50)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PromoteRequest(BaseModel):
    name: str = Field(min_length=1)
    dim: int
    api_url: str = Field(min_length=1)
    model_arg: str = Field(min_length=1)


class CutoverRequest(BaseModel):
    force: bool = False
