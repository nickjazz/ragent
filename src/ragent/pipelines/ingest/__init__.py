"""C4 / T2v.30-T2v.39 — v2 ingest pipeline factory.

Graph: ``_TextLoader → _MimeAwareSplitter → _BudgetChunker →
DocumentEmbedder`` (ES only; embedder bulk-writes directly).

Splitter dispatches per ``meta["mime_type"]``:
- ``text/plain``      → Haystack ``DocumentSplitter`` (passage)
- ``text/markdown``   → ``_MarkdownASTSplitter`` (mistletoe)
- ``text/html``       → ``_HtmlASTSplitter`` (selectolax)
- ``application/pdf`` → ``_PdfASTSplitter`` (pymupdf4llm to_markdown + RapidOCR auto)

Each splitter emits atoms whose ``meta["raw_content"]`` is the original
byte slice (markdown fences / HTML fragments preserved). ``_BudgetChunker``
is mime-agnostic: greedy-pack to ``CHUNK_TARGET_CHARS``, hard-split atoms
exceeding ``CHUNK_MAX_CHARS`` with ``CHUNK_OVERLAP_CHARS`` overlap.
"""

from __future__ import annotations

from typing import Any

from haystack.core.pipeline import Pipeline

from ragent.errors.codes import TaskErrorCode
from ragent.pipelines.observability import wrap_pipeline_component
from ragent.security.archive_guard import INGEST_MAX_PDF_PAGES  # noqa: F401 — re-exported

from ragent.pipelines.ingest.loader import ALLOWED_MIMES, _TextLoader  # noqa: F401
from ragent.pipelines.ingest.splitter import (  # noqa: F401
    INGEST_PDF_MARGIN_PTS,
    _DocxASTSplitter,
    _HtmlASTSplitter,
    _MarkdownASTSplitter,
    _MimeAwareSplitter,
    _PdfASTSplitter,
    _PptxASTSplitter,
    _SPLITTER_LABEL,
)
from ragent.pipelines.ingest.chunker import (  # noqa: F401
    CHUNK_MAX_CHARS,
    CHUNK_MAX_PIECES_PER_ATOM,
    CHUNK_OVERLAP_CHARS,
    CHUNK_TARGET_CHARS,
    _BudgetChunker,
    _pack_atoms,
    validate_chunk_config,
)
from ragent.pipelines.ingest.embedder import DocumentEmbedder  # noqa: F401


# ---------------------------------------------------------------------------
# build_ingest_pipeline — v2 graph
# ---------------------------------------------------------------------------


def build_ingest_pipeline(embedder: Any) -> Pipeline:
    """V2 ingest pipeline.

    Run input shape::

        {
            "loader": {
                "content": str,
                "mime_type": "text/plain"|"text/markdown"|"text/html",
                "document_id": str,
                "source_url": str | None,
                "source_title": str | None,
                ...
            }
        }

    Retry idempotency relies on ``_op_type: index`` (overwrite-by-id) in the
    embedder's bulk writes and deterministic chunk IDs (Haystack hashes
    content+meta). Chunks live only in ES; the v1 ``chunks`` DB table and
    ``ChunkRepository`` were dropped in C6.
    """
    pipeline = Pipeline()

    def _add(name: str, component: Any, *, step: str, error_code: str | None = None) -> None:
        kwargs: dict = {"namespace": "ingest", "step": step}
        if error_code is not None:
            kwargs["error_code"] = error_code
        pipeline.add_component(name, wrap_pipeline_component(component, **kwargs))

    _add("loader", _TextLoader(), step="load")
    _add(
        "splitter",
        _MimeAwareSplitter(),
        step="split",
        error_code=TaskErrorCode.PIPELINE_UNROUTABLE,
    )
    _add("chunker", _BudgetChunker(), step="chunker")
    _add("embedder", embedder, step="embedder", error_code=TaskErrorCode.EMBEDDER_ERROR)

    pipeline.connect("loader.documents", "splitter.documents")
    pipeline.connect("splitter.documents", "chunker.documents")
    pipeline.connect("chunker.documents", "embedder.documents")

    return pipeline
