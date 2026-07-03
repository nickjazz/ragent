"""T7.5e — Worker process entrypoint: python -m ragent.worker (B30)."""

from __future__ import annotations

import structlog

from ragent.bootstrap.guard import enforce
from ragent.bootstrap.init_schema import init_schema
from ragent.bootstrap.logging_config import configure_logging
from ragent.bootstrap.telemetry import setup_tracing
from ragent.utility.env import bool_env, int_env

_logger = structlog.get_logger(__name__)


def _patch_rapidocr() -> None:
    """Reconfigure the RapidOCR singleton with operator-tunable settings.

    rapidocr_api.ENGINE is a module-level singleton initialised with default
    ONNX settings (intra_op_num_threads=-1, i.e. ONNX picks 1).  Replacing it
    here — once at worker startup, before any task runs — lets all ingest tasks
    share a single properly-threaded engine without per-task overhead.

    Only runs when INGEST_PDF_USE_OCR=true; no-op otherwise.
    """
    if not bool_env("INGEST_PDF_USE_OCR", False):
        return
    try:
        from pymupdf4llm.ocr import rapidocr_api
        from rapidocr_onnxruntime import RapidOCR

        threads = int_env("INGEST_PDF_OCR_THREADS", 2)
        rapidocr_api.ENGINE = RapidOCR(intra_op_num_threads=threads)
    except Exception:
        _logger.warning("rapidocr_patch_failed", exc_info=True)


if __name__ == "__main__":  # pragma: no cover
    enforce()
    configure_logging("ragent-worker")
    setup_tracing("ragent-worker")
    init_schema()
    _patch_rapidocr()

    from taskiq.cli.worker.args import WorkerArgs
    from taskiq.cli.worker.run import start_listen

    start_listen(
        WorkerArgs(
            broker="ragent.bootstrap.broker:broker",
            modules=[
                "ragent.workers.ingest",
                "ragent.workers.backfill",
            ],
        ),
    )
