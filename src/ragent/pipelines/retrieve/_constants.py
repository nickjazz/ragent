"""Module-level constants for the retrieve pipeline (shared across sub-modules)."""

from __future__ import annotations

from ragent.utility.env import int_env, optional_float_env

# Spec §4.6 default; composition.py reads EXCERPT_MAX_CHARS env and threads
# the runtime value into build_retrieval_pipeline + create_{chat,retrieve}_router
# so doc_to_source_entry and _ExcerptTruncator share one value.
EXCERPT_MAX_CHARS_DEFAULT = 512
# Upper bound on top_k — pinned by spec §3.4.4 (`POST /retrieve/v1` Pydantic
# `le=200`) and §3.8.3 (MCP retrieve tool `maximum: 200`). DEFAULT_TOP_K is the
# fallback when callers omit `top_k`; if an operator sets RETRIEVAL_TOP_K above
# the advertised maximum, MCP clients calling with omitted top_k would silently
# over-fetch past the contract. Fast-fail at boot instead.
MAX_TOP_K = 200
DEFAULT_TOP_K = int_env("RETRIEVAL_TOP_K", 20)
DEFAULT_MIN_SCORE: float | None = optional_float_env("RETRIEVAL_MIN_SCORE")
_VALID_MODES = frozenset({"rrf", "concatenate", "vector_only", "bm25_only"})
