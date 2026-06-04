"""Retrieval env-var defaults shared by schemas and the pipeline."""

from __future__ import annotations

from ragent.utility.env import int_env, optional_float_env

DEFAULT_TOP_K: int = int_env("RETRIEVAL_TOP_K", 20)
DEFAULT_MIN_SCORE: float | None = optional_float_env("RETRIEVAL_MIN_SCORE")
