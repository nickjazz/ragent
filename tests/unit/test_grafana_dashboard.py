"""P2.1 — Grafana dashboard JSON validation tests.

Validates that `deploy/grafana/ragent_overview.json` exists and contains
the required observability panels so that the dashboard stays in sync with
the metrics exported by the application.

Run with:  uv run pytest tests/unit/test_grafana_dashboard.py -x
"""

from __future__ import annotations

import json
from pathlib import Path

_DASHBOARD_PATH = (
    Path(__file__).parent.parent.parent / "deploy" / "grafana" / "ragent_overview.json"
)

# Panel titles that must be present — any removal is a breaking change.
_REQUIRED_PANEL_TITLES = frozenset(
    {
        "Ingest Pipeline Rate",
        "Ingest Failure Rate",
        "Worker Pipeline Duration (p99)",
        "Reranker Degradation Rate",
        "Reconciler Health",
        "Readyz Probe Status",
    }
)


def _load_dashboard() -> dict:
    return json.loads(_DASHBOARD_PATH.read_text())


def test_dashboard_file_exists() -> None:
    assert _DASHBOARD_PATH.exists(), f"Missing Grafana dashboard: {_DASHBOARD_PATH}"


def test_dashboard_has_title() -> None:
    dash = _load_dashboard()
    assert dash.get("title"), "Dashboard JSON must have a non-empty 'title' field"


def test_dashboard_has_panels() -> None:
    dash = _load_dashboard()
    assert isinstance(dash.get("panels"), list), "'panels' must be a list"
    assert len(dash["panels"]) > 0, "Dashboard must have at least one panel"


def test_all_required_panels_present() -> None:
    dash = _load_dashboard()
    titles = {p.get("title") for p in dash.get("panels", [])}
    missing = _REQUIRED_PANEL_TITLES - titles
    assert not missing, f"Required panels missing from dashboard: {sorted(missing)}"


def test_dashboard_has_uid() -> None:
    dash = _load_dashboard()
    assert dash.get("uid"), "Dashboard JSON must have a non-empty 'uid' field"
