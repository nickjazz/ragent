"""T-CAUI.1 — AGENTIC_UI_TOOL envelope contract (name + unwrap)."""

from __future__ import annotations

import json

import pytest
from twp_ai.client_tools import AGENTIC_UI_TOOL_NAME, unwrap_agentic_ui_call


def test_name_constant() -> None:
    assert AGENTIC_UI_TOOL_NAME == "AGENTIC_UI_TOOL"


def test_unwrap_returns_inner_name_and_arguments_json() -> None:
    envelope = json.dumps({"tool_name": "fill_form", "arguments": {"description": "優化後的文案"}})

    name, args_json = unwrap_agentic_ui_call(envelope)

    assert name == "fill_form"
    # arguments come back as a JSON string (the TOOL_CALL_ARGS.delta wire shape),
    # non-ASCII preserved so the frontend gets the literal value.
    assert json.loads(args_json) == {"description": "優化後的文案"}
    assert "優化後的文案" in args_json


def test_unwrap_defaults_missing_arguments_to_empty_object() -> None:
    name, args_json = unwrap_agentic_ui_call(json.dumps({"tool_name": "ping"}))

    assert name == "ping"
    assert json.loads(args_json) == {}


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        json.dumps(["not", "an", "object"]),
        json.dumps({"arguments": {"x": 1}}),  # missing tool_name
        json.dumps({"tool_name": "", "arguments": {}}),  # empty tool_name
        json.dumps({"tool_name": 42, "arguments": {}}),  # non-string tool_name
    ],
)
def test_unwrap_malformed_envelope_raises_value_error(bad: str) -> None:
    with pytest.raises(ValueError):
        unwrap_agentic_ui_call(bad)
