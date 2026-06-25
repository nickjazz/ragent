"""Shared test fixtures/helpers for the twp-ai test suite."""

from __future__ import annotations

import contextlib
import io
import logging
from collections.abc import Callable, Iterator

import pytest


@pytest.fixture
def captured_logger_text() -> Callable[[str], contextlib.AbstractContextManager[io.StringIO]]:
    """Capture a named stdlib logger's formatted output directly.

    Bypasses pytest's ``caplog``/root-logger capture, which is empirically
    flaky under coverage instrumentation when many tests share a session
    (see docs/00_journal.md 2026-05-20 "Subprocess Isolation" and
    docs/00_rule.md "Test Log Capture").
    """

    @contextlib.contextmanager
    def _capture(logger_name: str) -> Iterator[io.StringIO]:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        try:
            yield stream
        finally:
            logger.removeHandler(handler)

    return _capture
