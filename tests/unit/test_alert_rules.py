"""P2.1 — Alerting rules validation tests.

Validates that `deploy/prometheus/alerts.yaml` contains the required alert
definitions with correct structure.  These tests guard against:
  - Alert names being accidentally deleted or renamed.
  - Missing required Prometheus alert fields (expr, for, labels, annotations).
  - Severity labels outside the allowed set.

Run with:  uv run pytest tests/unit/test_alert_rules.py -x
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Required alert names — any removal is a breaking change.
_REQUIRED_ALERTS = frozenset(
    {
        "ReconcilerTickStalled",
        "IngestHighFailureRate",
        "RerankerDegradedPersistent",
        "ReadyzProbeFailing",
        "WorkerPipelineSlow",
    }
)

_ALLOWED_SEVERITIES = frozenset({"critical", "warning"})

_ALERTS_PATH = Path(__file__).parent.parent.parent / "deploy" / "prometheus" / "alerts.yaml"


def _load_alerts() -> list[dict]:
    """Return the flat list of all alert rules from the YAML."""
    data = yaml.safe_load(_ALERTS_PATH.read_text())
    rules: list[dict] = []
    for group in data.get("groups", []):
        for rule in group.get("rules", []):
            if "alert" in rule:  # skip recording rules
                rules.append(rule)
    return rules


def test_alerts_yaml_exists() -> None:
    assert _ALERTS_PATH.exists(), f"Missing alerts file: {_ALERTS_PATH}"


def test_all_required_alerts_present() -> None:
    rules = _load_alerts()
    names = {r["alert"] for r in rules}
    missing = _REQUIRED_ALERTS - names
    assert not missing, f"Required alerts missing from {_ALERTS_PATH.name}: {sorted(missing)}"


@pytest.mark.parametrize("rule", _load_alerts() if _ALERTS_PATH.exists() else [])
def test_alert_has_required_fields(rule: dict) -> None:
    name = rule.get("alert", "<unknown>")
    assert "expr" in rule, f"{name}: missing 'expr'"
    assert "for" in rule, f"{name}: missing 'for'"
    assert "labels" in rule, f"{name}: missing 'labels'"
    assert "annotations" in rule, f"{name}: missing 'annotations'"
    assert "summary" in rule.get("annotations", {}), f"{name}: missing annotations.summary"
    severity = rule.get("labels", {}).get("severity")
    assert severity in _ALLOWED_SEVERITIES, (
        f"{name}: severity {severity!r} not in {_ALLOWED_SEVERITIES}"
    )
