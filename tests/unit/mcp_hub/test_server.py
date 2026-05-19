"""Server entry-point env-var validation and ASGI app composition."""

from __future__ import annotations

import json
import logging

import pytest
import structlog
from fastmcp import FastMCP

from ragent.mcp_hub.mcp_hub import HubBundle
from ragent.mcp_hub.server import build_app, main


def test_non_numeric_port_exits_with_clear_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("MCP_HUB_PORT", "not-a-number")
    monkeypatch.setenv("MCP_HUB_TOOLS_YAML", "/nonexistent.yaml")

    with pytest.raises(SystemExit) as ex:
        main()

    assert "MCP_HUB_PORT" in str(ex.value)


class _FakeClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        if self.fail:
            raise RuntimeError("simulated client teardown failure")


@pytest.mark.asyncio
async def test_build_app_closes_all_clients_and_isolates_failures(
    caplog: pytest.LogCaptureFixture,
):
    """Lifespan shutdown closes every per-system httpx client, and a failing
    aclose() on one client does not prevent siblings from closing — the
    failure surfaces as a `mcp_hub.shutdown_error` log event."""
    structlog.configure(
        processors=[structlog.processors.add_log_level, structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    caplog.set_level(logging.ERROR)

    bad, good = _FakeClient(fail=True), _FakeClient()
    bundle = HubBundle(
        hub=FastMCP("test-hub"),
        clients={"bad": bad, "good": good},
        failures=[],
    )

    asgi = build_app(bundle, path="/mcp")
    fastmcp_app = asgi.app
    composed = fastmcp_app.router.lifespan_context

    async with composed(fastmcp_app):
        pass

    assert bad.closed and good.closed, "every client must receive aclose()"
    events = [json.loads(r.message) for r in caplog.records]
    shutdown_events = [e for e in events if e.get("event") == "mcp_hub.shutdown_error"]
    assert len(shutdown_events) == 1
    assert shutdown_events[0]["system"] == "bad"
