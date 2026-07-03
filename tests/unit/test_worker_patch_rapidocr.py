"""Unit tests for _patch_rapidocr — worker-startup RapidOCR ENGINE configuration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _call_patch(*, use_ocr: bool, threads: int = 2) -> None:
    """Invoke _patch_rapidocr with controlled env vars."""
    import ragent.worker as worker_mod

    with (
        patch.object(worker_mod, "bool_env", return_value=use_ocr),
        patch.object(worker_mod, "int_env", return_value=threads),
    ):
        worker_mod._patch_rapidocr()


def test_patch_rapidocr_noop_when_ocr_disabled() -> None:
    """When INGEST_PDF_USE_OCR=false, rapidocr_api is never touched."""
    fake_rapidocr_api = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "pymupdf4llm.ocr": MagicMock(rapidocr_api=fake_rapidocr_api),
            "rapidocr_onnxruntime": MagicMock(),
        },
    ):
        _call_patch(use_ocr=False)

    assert "ENGINE" not in vars(fake_rapidocr_api)


def test_patch_rapidocr_replaces_engine_when_ocr_enabled() -> None:
    """When INGEST_PDF_USE_OCR=true, rapidocr_api.ENGINE is replaced with a
    RapidOCR instance using the configured thread count."""
    fake_api = MagicMock()
    fake_engine_class = MagicMock(return_value=MagicMock(name="NewEngine"))
    fake_rapidocr_mod = MagicMock()
    fake_rapidocr_mod.RapidOCR = fake_engine_class

    with patch.dict(
        "sys.modules",
        {
            "pymupdf4llm": MagicMock(),
            "pymupdf4llm.ocr": MagicMock(rapidocr_api=fake_api),
            "rapidocr_onnxruntime": fake_rapidocr_mod,
        },
    ):
        import ragent.worker as worker_mod

        with (
            patch.object(worker_mod, "bool_env", return_value=True),
            patch.object(worker_mod, "int_env", return_value=4),
        ):
            worker_mod._patch_rapidocr()

    fake_engine_class.assert_called_once_with(intra_op_num_threads=4)
    assert fake_engine_class.return_value == fake_api.ENGINE


def test_patch_rapidocr_logs_warning_on_import_failure() -> None:
    """When OCR deps are absent, _patch_rapidocr logs a warning and doesn't raise."""
    import structlog.testing

    import ragent.worker as worker_mod

    with (
        patch.object(worker_mod, "bool_env", return_value=True),
        patch.dict("sys.modules", {"pymupdf4llm.ocr": None}),
        structlog.testing.capture_logs() as logs,
    ):
        worker_mod._patch_rapidocr()  # must not raise

    assert any(log.get("event") == "rapidocr_patch_failed" for log in logs)
