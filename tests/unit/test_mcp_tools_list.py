"""Pin the MCP `tools/list` contract for the retrieve tool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from haystack.core.pipeline import Pipeline

from ragent.routers.mcp import create_mcp_router


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("RAGENT_MCP_RETRIEVE_SOURCE_APP_ALLOWLIST", raising=False)
    app = FastAPI()
    app.include_router(create_mcp_router(retrieval_pipeline=MagicMock(spec=Pipeline)))
    with TestClient(app) as c:
        yield c


def _tool(client: TestClient) -> dict:
    return client.post("/mcp/v1", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()[
        "result"
    ]["tools"][0]


def test_tools_list_returns_exactly_one_tool(client: TestClient) -> None:
    resp = client.post(
        "/mcp/v1",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    tools = body["result"]["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 1


def test_tools_list_advertises_retrieve_tool(client: TestClient) -> None:
    tool = _tool(client)
    assert tool["name"] == "retrieve"
    assert isinstance(tool["description"], str)
    assert "retriev" in tool["description"].lower()


def test_retrieve_tool_has_readonly_hint(client: TestClient) -> None:
    tool = _tool(client)
    assert tool.get("annotations", {}).get("readOnlyHint") is True


def test_retrieve_input_schema_required_fields(client: TestClient) -> None:
    schema = _tool(client)["inputSchema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["query"]
    assert schema["additionalProperties"] is False


def test_retrieve_input_schema_property_types(client: TestClient) -> None:
    props = _tool(client)["inputSchema"]["properties"]

    assert props["query"]["type"] == "string"
    assert props["query"]["minLength"] == 1
    assert isinstance(props["query"]["description"], str)

    assert props["top_k"]["type"] == "integer"
    assert props["top_k"]["minimum"] == 1
    assert props["top_k"]["maximum"] == 200
    assert props["top_k"]["default"] == 20
    assert isinstance(props["top_k"]["description"], str)

    assert props["dedupe"]["type"] == "boolean"
    assert props["dedupe"]["default"] is False
    assert isinstance(props["dedupe"]["description"], str)


def test_retrieve_input_schema_hides_internal_fields(client: TestClient) -> None:
    props = _tool(client)["inputSchema"]["properties"]
    assert "source_meta" not in props
    assert "min_score" not in props


def test_retrieve_input_schema_no_pydantic_titles(client: TestClient) -> None:
    props = _tool(client)["inputSchema"]["properties"]
    for name, prop in props.items():
        assert "title" not in prop, f"property {name!r} must not expose a Pydantic title"


def test_retrieve_input_schema_hides_source_app_when_allowlist_unset(
    client: TestClient,
) -> None:
    props = _tool(client)["inputSchema"]["properties"]
    assert "source_app" not in props


def test_retrieve_input_schema_source_app_enum_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAGENT_MCP_RETRIEVE_SOURCE_APP_ALLOWLIST", " confluence, slack ,,")
    app = FastAPI()
    app.include_router(create_mcp_router(retrieval_pipeline=MagicMock(spec=Pipeline)))
    with TestClient(app) as c:
        props = _tool(c)["inputSchema"]["properties"]

    assert props["source_app"]["type"] == "string"
    assert props["source_app"]["minLength"] == 1
    assert props["source_app"]["maxLength"] == 64
    assert props["source_app"]["enum"] == ["confluence", "slack"]
    assert isinstance(props["source_app"]["description"], str)
    assert not any(key.startswith("x-mcp-") for key in props["source_app"])
