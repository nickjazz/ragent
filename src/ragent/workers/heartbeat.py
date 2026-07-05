"""T3.2b / TA.10 — Worker heartbeat: periodic updated_at refresh (B16).

`run_heartbeat` runs in a plain threading.Thread. It receives a sync `tick`
callable so no asyncio event loop is needed, avoiding cross-loop AsyncEngine
issues. The caller (composition root) owns the tick implementation.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import structlog

logger = structlog.get_logger(__name__)


def run_heartbeat(
    document_id: str,
    tick: Callable[[str], None],
    stop: threading.Event,
    interval: float = 30.0,
) -> None:
    while not stop.wait(timeout=interval):
        try:
            tick(document_id)
        except Exception as exc:
            logger.warning("heartbeat.update_failed", document_id=document_id, error=str(exc))
