"""T-CAT.10 — ChatAttachmentPipeline: load → unprotect → AST build."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import anyio
import structlog
from haystack.dataclasses import Document

from ragent.pipelines.ingest.splitter import _MD_HEADING_RE, _MimeAwareSplitter
from ragent.schemas.attachments import BINARY_MIMES, UNPROTECT_MIMES, AttachmentMime

if TYPE_CHECKING:
    from ragent.clients.unprotect import UnprotectClient

logger = structlog.get_logger(__name__)

# HTML heading atoms carry their tag in meta["raw_content"] (e.g. "<h1>...").
# Markdown/PDF heading atoms are detected via the splitter's own _MD_HEADING_RE.
_HTML_HEADING_RE = re.compile(r"^<h[1-6][\s>]", re.IGNORECASE)


def _is_heading_atom(atom: Document) -> bool:
    raw = atom.meta.get("raw_content") or ""
    return bool(_MD_HEADING_RE.match(raw) or _HTML_HEADING_RE.match(raw))


def _build_simplified(atoms: list[Document]) -> list[Document]:
    """Per docs/spec/chat_attachments.md §4: title + first two lines per
    section, derived from `atoms` (the complete AST) by a single tree-walk —
    no new per-format parsing. Sections are delimited by heading atoms;
    formats with no heading atoms (docx/pptx/csv/plain text) collapse to one
    section over the whole list.
    """
    if not atoms:
        return []

    sections: list[list[Document]] = []
    current: list[Document] = []
    for atom in atoms:
        if _is_heading_atom(atom) and current:
            sections.append(current)
            current = []
        current.append(atom)
    sections.append(current)

    simplified: list[Document] = []
    for section in sections:
        head = section[0]
        if _is_heading_atom(head):
            title, body = head.content or "", section[1:]
        else:
            title, body = "", section

        first_two_lines: list[str] = []
        for atom in body:
            for line in (atom.content or "").splitlines():
                if line.strip():
                    first_two_lines.append(line)
                    if len(first_two_lines) == 2:
                        break
            if len(first_two_lines) == 2:
                break

        text = "\n".join([title, *first_two_lines]) if title else "\n".join(first_two_lines)
        simplified.append(Document(content=text, meta={"mime_type": head.meta.get("mime_type")}))
    return simplified


class ChatAttachmentPipeline:
    """Load attachment file → optional unprotect → build AST.

    Returns "complete" (full AST) and "simplified" (title + first two lines
    per section, derived in memory — see `_build_simplified`).
    """

    def __init__(self, unprotect_client: UnprotectClient | None):
        self._unprotect_client = unprotect_client
        self._splitter = _MimeAwareSplitter()

    async def run(
        self,
        file_bytes: bytes,
        mime_type: AttachmentMime,
        *,
        user_id: str = "anonymous",
        filename: str = "attachment",
    ) -> dict[str, list[Document]]:
        """Run the attachment pipeline: load → unprotect → AST build.

        Args:
            file_bytes: Raw file content as bytes
            mime_type: AttachmentMime type of the file
            user_id: Uploading user, forwarded to the unprotect API (delegated user)
            filename: Original filename, forwarded to the unprotect API

        Returns:
            dict with "complete" and "simplified" keys, each containing list[Document]
        """
        content_bytes = file_bytes

        # Per docs/spec/chat_attachments.md §3: skipped when no unprotect_client is
        # wired, or (fail-soft) when the call raises — original bytes are used as a
        # fallback in both cases. The unprotect API is synchronous network I/O, so it
        # runs off-loop.
        if mime_type in UNPROTECT_MIMES and self._unprotect_client is not None:
            try:
                content_bytes = await anyio.to_thread.run_sync(
                    self._unprotect_client.unprotect, file_bytes, user_id, filename
                )
            except Exception as exc:
                logger.warning(
                    "chat_attachment.unprotect_failed_fallback",
                    filename=filename,
                    mime_type=mime_type.value,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        if mime_type in BINARY_MIMES:
            doc = Document(
                content=None,
                meta={"mime_type": mime_type.value, "raw_bytes": content_bytes},
            )
        else:
            content_str = content_bytes.decode("utf-8")
            doc = Document(content=content_str, meta={"mime_type": mime_type.value})

        atoms = self._splitter.run([doc])["documents"]
        simplified = _build_simplified(atoms)

        logger.info(
            "chat_attachment.pipeline_completed",
            filename=filename,
            mime_type=mime_type.value,
            atom_count=len(atoms),
        )

        return {
            "complete": atoms,
            "simplified": simplified,
        }
