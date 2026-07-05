"""Covers the import surface of ragent.worker (process entrypoint).

The module only defines imports + __main__ block; the __main__ block is
marked # pragma: no cover; the import lines are covered here.
Heartbeat behaviour tests live in tests/unit/test_heartbeat.py.
"""

import ragent.worker  # noqa: F401  — covers module-level import lines
