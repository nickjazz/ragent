"""_BudgetChunker and helpers for the v2 ingest pipeline."""

from __future__ import annotations

from typing import Any

from haystack.core.component import component
from haystack.dataclasses import Document

from ragent.errors.codes import TaskErrorCode
from ragent.pipelines.observability import IngestStepError
from ragent.utility.env import int_env

# Single mime-agnostic budget profile (replaces v1 EN/CJK/CSV constants).
CHUNK_TARGET_CHARS = int_env("CHUNK_TARGET_CHARS", 1000)
CHUNK_MAX_CHARS = int_env("CHUNK_MAX_CHARS", 1500)
CHUNK_OVERLAP_CHARS = int_env("CHUNK_OVERLAP_CHARS", 100)
# Per-atom hard-split safety cap: an atom that exceeds this many pieces is
# treated as misconfiguration (overlap ≥ target produces tiny advance steps
# and can blow up to millions of chunks on a 1 MB atom).
CHUNK_MAX_PIECES_PER_ATOM = int_env("CHUNK_MAX_PIECES_PER_ATOM", 10_000)


def validate_chunk_config() -> None:
    """Bootstrap-time invariant check.

    Called from ``bootstrap.guard.enforce`` so a misconfigured env aborts
    process boot cleanly rather than crashing every importer (tests,
    linters) with a module-level RuntimeError.
    """
    if CHUNK_TARGET_CHARS <= CHUNK_OVERLAP_CHARS:
        raise RuntimeError(
            "CHUNK_TARGET_CHARS must be > CHUNK_OVERLAP_CHARS; "
            f"got target={CHUNK_TARGET_CHARS}, overlap={CHUNK_OVERLAP_CHARS}"
        )


# ---------------------------------------------------------------------------
# _BudgetChunker (T2v.36/37 — replaces _CharBudgetChunker)
# ---------------------------------------------------------------------------


@component
class _BudgetChunker:
    """Mime-agnostic budget chunker.

    Greedy-packs atoms into chunks ≤ ``CHUNK_TARGET_CHARS`` joined by
    newlines. Atoms longer than ``CHUNK_MAX_CHARS`` are hard-split with
    ``CHUNK_OVERLAP_CHARS`` overlap. Each output ``Document`` carries:
    - ``content``: packed normalized text
    - ``meta["raw_content"]``: concatenation of source atoms' raw slices
    - ``meta["split_id"]``: zero-based per-document index
    """

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict:
        result: list[Document] = []
        # Group by document_id so split_id resets per source document.
        groups: dict[Any, list[Document]] = {}
        order: list[Any] = []
        for d in documents:
            key = d.meta.get("document_id")
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(d)

        for doc_id in order:
            atoms = groups[doc_id]
            if not atoms:
                continue
            base_meta = {**atoms[0].meta}
            base_meta.pop("raw_content", None)
            chunks = _pack_atoms(atoms)
            for i, (text, raw) in enumerate(chunks):
                meta = {**base_meta, "split_id": i, "raw_content": raw}
                result.append(Document(content=text, meta=meta))
        return {"documents": result}


def _pack_atoms(atoms: list[Document]) -> list[tuple[str, str]]:
    """Pack atoms into chunks ≤ ``CHUNK_TARGET_CHARS``.

    Text overlap (``CHUNK_OVERLAP_CHARS`` chars carried tail→head between
    adjacent chunks) is for retrieval continuity. ``raw_content`` overlap
    is intentionally NOT carried at character granularity — naive
    ``raw[-overlap:]`` slicing cuts through HTML tags / Markdown
    markers and produces malformed fragments. Instead, ``raw_content`` is
    always atom-aligned: each chunk's raw is the concatenation of the
    raws of the atoms whose normalized text appears in that chunk
    (excluding the carried-overlap prefix).
    """
    target = CHUNK_TARGET_CHARS
    max_chars = CHUNK_MAX_CHARS
    overlap = CHUNK_OVERLAP_CHARS
    chunks: list[tuple[str, str]] = []
    buf_text = ""
    buf_raw = ""

    def flush(carry: bool = True) -> None:
        nonlocal buf_text, buf_raw
        if not buf_text:
            return
        chunks.append((buf_text, buf_raw))
        if carry and overlap > 0 and len(buf_text) > overlap:
            # Carry text overlap for retrieval continuity. Reset raw to ""
            # so the next chunk's raw starts fresh at the next atom
            # boundary — keeps raw_content well-formed for downstream
            # citation rendering.
            buf_text = buf_text[-overlap:]
            buf_raw = ""
        else:
            buf_text = ""
            buf_raw = ""

    for atom in atoms:
        text = atom.content or ""
        raw = atom.meta.get("raw_content") or text
        if not text:
            continue
        if len(text) > max_chars:
            flush(carry=False)
            step = max(1, target - overlap)
            start = 0
            pieces = 0
            while start < len(text):
                end = min(start + target, len(text))
                piece = text[start:end]
                # When raw and text are length-aligned (e.g. plain-text
                # atoms where raw == text), slice raw to match. Otherwise
                # (HTML/markdown atoms whose raw is much larger than the
                # normalized text) fall back to the text piece itself —
                # duplicating the full raw across every hard-split piece
                # would multiply ES storage and LLM tokens by N.
                raw_piece = raw[start:end] if len(raw) == len(text) else piece
                chunks.append((piece, raw_piece))
                pieces += 1
                if pieces > CHUNK_MAX_PIECES_PER_ATOM:
                    raise IngestStepError(
                        f"atom exceeded {CHUNK_MAX_PIECES_PER_ATOM} hard-split pieces "
                        f"(target={target}, overlap={overlap}, atom_len={len(text)})",
                        error_code=TaskErrorCode.CHUNK_BUDGET_EXCEEDED,
                    )
                if end == len(text):
                    break
                start += step
            continue
        sep = "\n" if buf_text else ""
        if buf_text and len(buf_text) + len(sep) + len(text) > target:
            flush(carry=True)
            sep = "\n" if buf_text else ""
        buf_text = buf_text + sep + text
        # Raw is joined only when buf_raw is non-empty (i.e. previous
        # atom contributed); after a flush(carry=True) buf_raw is reset
        # to "" so this chunk's raw begins at the current atom boundary.
        raw_sep = "\n" if buf_raw else ""
        buf_raw = buf_raw + raw_sep + raw
    if buf_text:
        chunks.append((buf_text, buf_raw))
    return chunks
