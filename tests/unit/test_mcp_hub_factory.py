"""T-MH-UV.1 — build_mcp_app() is a valid 0-arg uvicorn --factory callable."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP

from ragent.mcp_hub.mcp_hub import HubBundle
from ragent.mcp_hub.server import build_mcp_app


def _fake_bundle() -> HubBundle:
    return HubBundle(hub=FastMCP("fake"), clients={}, failures=[])


def test_build_mcp_app_is_importable_and_callable():
    """build_mcp_app exists as a 0-arg callable — satisfies uvicorn --factory contract."""
    assert callable(build_mcp_app)


def test_build_mcp_app_returns_asgi_app(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """build_mcp_app() returns an ASGI-compatible callable (has __call__)."""
    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text("name: test\ntools: []\n")
    monkeypatch.setenv("MCP_HUB_TOOLS_YAML", str(tools_yaml))

    with patch("ragent.mcp_hub.server.build_hub", return_value=_fake_bundle()):
        app = build_mcp_app()

    assert callable(app)


def test_build_mcp_app_forwards_path_flag(monkeypatch: pytest.MonkeyPatch):
    """MCP_HUB_PATH env var is forwarded to build_app() as the mount path."""
    monkeypatch.setenv("MCP_HUB_PATH", "/custom-mcp")

    mock_build_app = MagicMock(return_value=MagicMock())
    with (
        patch("ragent.mcp_hub.server.build_hub", return_value=_fake_bundle()),
        patch("ragent.mcp_hub.server.build_app", mock_build_app),
    ):
        build_mcp_app()

    _, kwargs = mock_build_app.call_args
    assert kwargs["path"] == "/custom-mcp"


def test_build_mcp_app_forwards_stateless_and_json_flags(monkeypatch: pytest.MonkeyPatch):
    """MCP_HUB_STATELESS_HTTP and MCP_HUB_JSON_RESPONSE env vars are forwarded."""
    monkeypatch.setenv("MCP_HUB_STATELESS_HTTP", "true")
    monkeypatch.setenv("MCP_HUB_JSON_RESPONSE", "1")

    mock_build_app = MagicMock(return_value=MagicMock())
    with (
        patch("ragent.mcp_hub.server.build_hub", return_value=_fake_bundle()),
        patch("ragent.mcp_hub.server.build_app", mock_build_app),
    ):
        build_mcp_app()

    _, kwargs = mock_build_app.call_args
    assert kwargs["stateless_http"] is True
    assert kwargs["json_response"] is True


def test_main_still_validates_non_numeric_port(monkeypatch: pytest.MonkeyPatch):
    """Port validation remains in main(), not in build_mcp_app()."""
    monkeypatch.setenv("MCP_HUB_PORT", "not-a-number")

    from ragent.mcp_hub.server import main

    with pytest.raises(SystemExit):
        main()
