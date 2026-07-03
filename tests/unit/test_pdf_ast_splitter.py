"""TDD — _PdfASTSplitter: PDF binary → Document atoms via pymupdf4llm per-page to_markdown."""

from __future__ import annotations


def _make_pdf_bytes(pages: list[str]) -> bytes:
    """Build a minimal PDF in-memory. Each string becomes one page of text."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(False)
    for text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, text=text)
    return bytes(pdf.output())


def _run_splitter(data: bytes) -> list:
    from haystack.dataclasses import Document as HDoc

    from ragent.pipelines.ingest import _PdfASTSplitter

    splitter = _PdfASTSplitter()
    doc = HDoc(
        content=None,
        meta={"mime_type": "application/pdf", "document_id": "doc-pdf", "raw_bytes": data},
    )
    return splitter.run([doc])["documents"]


# ---------------------------------------------------------------------------
# Page-level atoms
# ---------------------------------------------------------------------------


def test_pdf_at_least_one_atom_per_nonempty_page():
    data = _make_pdf_bytes(["Page one content", "Page two content"])
    atoms = _run_splitter(data)
    page_numbers = {a.meta["page_number"] for a in atoms}
    assert page_numbers == {1, 2}


def test_pdf_content_contains_page_text():
    data = _make_pdf_bytes(["Hello World"])
    atoms = _run_splitter(data)
    assert len(atoms) >= 1
    assert any("Hello World" in a.content for a in atoms)


def test_pdf_raw_content_set():
    data = _make_pdf_bytes(["Test content"])
    atoms = _run_splitter(data)
    assert len(atoms) >= 1
    assert all("raw_content" in a.meta for a in atoms)
    assert any("Test content" in a.meta["raw_content"] for a in atoms)


def test_pdf_meta_passthrough():
    data = _make_pdf_bytes(["text"])
    atoms = _run_splitter(data)
    assert len(atoms) >= 1
    for atom in atoms:
        assert atom.meta["document_id"] == "doc-pdf"
        assert atom.meta["mime_type"] == "application/pdf"


def test_pdf_empty_bytes_skipped():
    """Documents with no raw_bytes payload are skipped without raising."""
    atoms = _run_splitter(b"")
    assert atoms == []


def test_pdf_empty_page_skipped():
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(False)
    pdf.add_page()  # blank page — no content
    atoms = _run_splitter(bytes(pdf.output()))
    assert atoms == []


def test_pdf_page_number_in_meta():
    data = _make_pdf_bytes(["First", "Second", "Third"])
    atoms = _run_splitter(data)
    page_numbers = {a.meta.get("page_number") for a in atoms}
    assert page_numbers == {1, 2, 3}


# ---------------------------------------------------------------------------
# to_markdown integration — splitter calls pymupdf4llm per page
# ---------------------------------------------------------------------------


def test_pdf_splitter_calls_to_markdown_per_page(monkeypatch):
    """to_markdown is called once per page with pages=[i]; use_ocr=False by default."""
    from unittest.mock import patch

    calls = []

    def fake_to_markdown(pdf, *, pages, use_ocr, **kwargs):
        calls.append(pages)
        return f"Page {pages[0]} content\n"

    with patch("ragent.pipelines.ingest.splitter.pymupdf4llm") as mock_module:
        mock_module.to_markdown.side_effect = fake_to_markdown
        data = _make_pdf_bytes(["Alpha", "Beta"])
        _run_splitter(data)

    assert len(calls) == 2
    assert calls[0] == [0]
    assert calls[1] == [1]


def test_pdf_empty_markdown_page_skipped(monkeypatch):
    """Pages where to_markdown returns blank markdown produce no atoms."""
    from unittest.mock import patch

    with patch("ragent.pipelines.ingest.splitter.pymupdf4llm") as mock_module:
        mock_module.to_markdown.return_value = "   "
        data = _make_pdf_bytes(["anything"])
        atoms = _run_splitter(data)

    assert atoms == []


def test_pdf_to_markdown_fallback_on_failure(monkeypatch):
    """When to_markdown raises, falls back to plain fitz text; page is still ingested."""
    from unittest.mock import patch

    with patch("ragent.pipelines.ingest.splitter.pymupdf4llm") as mock_module:
        mock_module.to_markdown.side_effect = RuntimeError("rapidocr internal error")
        data = _make_pdf_bytes(["Fallback text"])
        atoms = _run_splitter(data)

    assert len(atoms) >= 1
    assert any("Fallback text" in a.content for a in atoms)


def test_pdf_store_shrink_called_once_per_page(monkeypatch):
    """MuPDF LRU cache is evicted after every page to bound peak RSS."""
    import fitz

    shrink_calls: list[int] = []
    monkeypatch.setattr(fitz.TOOLS, "store_shrink", lambda pct: shrink_calls.append(pct))

    data = _make_pdf_bytes(["Alpha", "Beta", "Gamma"])
    _run_splitter(data)
    assert shrink_calls == [100, 100, 100]


# ---------------------------------------------------------------------------
# T-SEC.5 — Page-count cap (defends against PDF page-count expansion bombs)
# ---------------------------------------------------------------------------


def test_pdf_page_count_exceeds_cap_raises(monkeypatch):
    """A PDF whose page_count exceeds INGEST_MAX_PDF_PAGES is rejected
    BEFORE the per-page extraction loop runs."""
    import pytest

    import ragent.pipelines.ingest.splitter as splitter_mod
    from ragent.security.archive_guard import PdfTooManyPagesError

    monkeypatch.setattr(splitter_mod, "INGEST_MAX_PDF_PAGES", 2)
    data = _make_pdf_bytes(["A", "B", "C"])  # 3 pages > cap of 2

    with pytest.raises(PdfTooManyPagesError) as exc_info:
        _run_splitter(data)

    exc = exc_info.value
    assert exc.http_status == 413
    assert exc.error_code == "INGEST_PDF_TOO_MANY_PAGES"
    assert exc.page_count == 3
    assert exc.cap == 2


def test_pdf_page_count_at_cap_passes(monkeypatch):
    """A PDF exactly at the cap is accepted (boundary)."""
    import ragent.pipelines.ingest.splitter as splitter_mod

    monkeypatch.setattr(splitter_mod, "INGEST_MAX_PDF_PAGES", 3)
    data = _make_pdf_bytes(["A", "B", "C"])  # exactly 3 pages
    atoms = _run_splitter(data)
    page_numbers = {a.meta["page_number"] for a in atoms}
    assert page_numbers == {1, 2, 3}


def test_pdf_max_pages_module_default():
    """Default cap is 2000 — generous for legitimate scanned reports."""
    from ragent.security.archive_guard import INGEST_MAX_PDF_PAGES

    assert INGEST_MAX_PDF_PAGES == 2000


# ---------------------------------------------------------------------------
# T-HDR.1 — PDF margins passed to to_markdown
# ---------------------------------------------------------------------------


def test_pdf_margins_passed_to_to_markdown(monkeypatch):
    """INGEST_PDF_MARGIN_PTS is forwarded as margins=(0,v,0,v) to to_markdown."""
    from unittest.mock import patch

    import ragent.pipelines.ingest.splitter as splitter_mod

    monkeypatch.setattr(splitter_mod, "INGEST_PDF_MARGIN_PTS", 50.0)
    received = {}

    def fake_to_markdown(pdf, *, pages, use_ocr, margins, **kwargs):
        received["margins"] = margins
        return "content\n"

    with patch("ragent.pipelines.ingest.splitter.pymupdf4llm") as mock_module:
        mock_module.to_markdown.side_effect = fake_to_markdown
        data = _make_pdf_bytes(["text"])
        _run_splitter(data)

    assert received["margins"] == (0, 50.0, 0, 50.0)


def test_pdf_margins_default_zero():
    """Default INGEST_PDF_MARGIN_PTS is 0 (no clipping)."""
    import ragent.pipelines.ingest.splitter as splitter_mod

    assert splitter_mod.INGEST_PDF_MARGIN_PTS == 0.0


# ---------------------------------------------------------------------------
# OCR — selective per-page use_ocr based on INGEST_PDF_USE_OCR
# ---------------------------------------------------------------------------


def _ocr_collector(received: list[bool]):
    """Return a fake to_markdown that appends use_ocr to received."""

    def fake(pdf, *, pages, use_ocr, **kwargs):
        received.append(use_ocr)
        return "content\n"

    return fake


def test_pdf_ocr_disabled_passes_use_ocr_false_to_every_page(monkeypatch):
    """When INGEST_PDF_USE_OCR=false (default), every page gets use_ocr=False."""
    from unittest.mock import patch

    import ragent.pipelines.ingest.splitter as splitter_mod

    monkeypatch.setattr(splitter_mod, "_PDF_USE_OCR", False)
    received: list[bool] = []

    with patch("ragent.pipelines.ingest.splitter.pymupdf4llm") as mock_module:
        mock_module.to_markdown.side_effect = _ocr_collector(received)
        data = _make_pdf_bytes(["Text page", "Another page"])
        _run_splitter(data)

    assert received == [False, False]


def test_pdf_ocr_enabled_text_pages_get_use_ocr_false(monkeypatch):
    """With OCR enabled, pages with enough chars get use_ocr=False."""
    from unittest.mock import patch

    import ragent.pipelines.ingest.splitter as splitter_mod

    monkeypatch.setattr(splitter_mod, "_PDF_USE_OCR", True)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_CHAR_THRESHOLD", 5)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_MAX_SCANNED_PAGES", 10)
    received: list[bool] = []

    with patch("ragent.pipelines.ingest.splitter.pymupdf4llm") as mock_module:
        mock_module.to_markdown.side_effect = _ocr_collector(received)
        # "Hello World" is 11 chars > threshold=5 → not scanned
        data = _make_pdf_bytes(["Hello World"])
        _run_splitter(data)

    assert received == [False]


def test_pdf_ocr_enabled_scanned_page_gets_use_ocr_true(monkeypatch):
    """With OCR enabled, a near-blank page (few chars) gets use_ocr=True."""
    from unittest.mock import patch

    import ragent.pipelines.ingest.splitter as splitter_mod

    # Make threshold very high so our test page is "scanned"
    monkeypatch.setattr(splitter_mod, "_PDF_USE_OCR", True)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_CHAR_THRESHOLD", 10000)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_MAX_SCANNED_PAGES", 10)
    received: list[bool] = []

    with patch("ragent.pipelines.ingest.splitter.pymupdf4llm") as mock_module:
        mock_module.to_markdown.side_effect = _ocr_collector(received)
        data = _make_pdf_bytes(["Hello"])
        _run_splitter(data)

    assert received == [True]


def test_pdf_ocr_enabled_dpi_forwarded_to_to_markdown(monkeypatch):
    """With OCR enabled, ocr_dpi from _PDF_OCR_DPI is forwarded to to_markdown."""
    from unittest.mock import patch

    import ragent.pipelines.ingest.splitter as splitter_mod

    monkeypatch.setattr(splitter_mod, "_PDF_USE_OCR", True)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_CHAR_THRESHOLD", 5)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_DPI", 72)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_MAX_SCANNED_PAGES", 10)
    received: dict[str, object] = {}

    def fake_to_markdown(pdf, *, pages, use_ocr, ocr_dpi, **kwargs):
        received["ocr_dpi"] = ocr_dpi
        return "content\n"

    with patch("ragent.pipelines.ingest.splitter.pymupdf4llm") as mock_module:
        mock_module.to_markdown.side_effect = fake_to_markdown
        data = _make_pdf_bytes(["Hello World"])
        _run_splitter(data)

    assert received["ocr_dpi"] == 72


# ---------------------------------------------------------------------------
# OCR scanned-page cap gate
# ---------------------------------------------------------------------------


def test_pdf_ocr_too_many_scanned_pages_raises(monkeypatch):
    """When pre-scan finds more scanned pages than the cap, raise PdfTooManyScannedPagesError
    BEFORE running to_markdown on any page."""
    import pytest

    import ragent.pipelines.ingest.splitter as splitter_mod
    from ragent.errors.codes import TaskErrorCode
    from ragent.security.archive_guard import PdfTooManyScannedPagesError

    # Set threshold so high that all pages are "scanned"; cap at 1 so 3 pages fails
    monkeypatch.setattr(splitter_mod, "_PDF_USE_OCR", True)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_CHAR_THRESHOLD", 10000)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_MAX_SCANNED_PAGES", 1)

    # pre-scan exits early: raises after finding cap+1=2 scanned pages, not all 3
    data = _make_pdf_bytes(["A", "B", "C"])

    with pytest.raises(PdfTooManyScannedPagesError) as exc_info:
        _run_splitter(data)

    exc = exc_info.value
    assert exc.scanned == 2  # cap+1: early exit stops at first violation
    assert exc.cap == 1
    assert exc.error_code == TaskErrorCode.INGEST_PDF_OCR_PAGES_EXCEEDED


def test_pdf_ocr_scanned_pages_at_cap_passes(monkeypatch):
    """Exactly at the scanned-page cap the document is accepted."""
    from unittest.mock import patch

    import ragent.pipelines.ingest.splitter as splitter_mod

    monkeypatch.setattr(splitter_mod, "_PDF_USE_OCR", True)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_CHAR_THRESHOLD", 10000)
    monkeypatch.setattr(splitter_mod, "_PDF_OCR_MAX_SCANNED_PAGES", 3)

    with patch("ragent.pipelines.ingest.splitter.pymupdf4llm") as mock_module:
        mock_module.to_markdown.return_value = "content\n"
        data = _make_pdf_bytes(["A", "B", "C"])  # exactly 3 == cap
        atoms = _run_splitter(data)

    assert len(atoms) == 3


def test_pdf_too_many_scanned_pages_error_class_contract():
    """PdfTooManyScannedPagesError carries scanned, cap, and the typed error_code."""
    from ragent.errors.codes import TaskErrorCode
    from ragent.security.archive_guard import PdfTooManyScannedPagesError

    exc = PdfTooManyScannedPagesError(scanned=15, cap=10)
    assert exc.scanned == 15
    assert exc.cap == 10
    assert exc.error_code == TaskErrorCode.INGEST_PDF_OCR_PAGES_EXCEEDED
    assert "15" in str(exc) and "10" in str(exc)
