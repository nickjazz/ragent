"""T-MCP.5 — Pin `tools/list` contract — exactly one tool `retrieve`
with document-scoped inputSchema (query + document_id_list required).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragent.routers.mcp import create_mcp_router
from tests.helpers import bypass_retrieve_v2_service


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.include_router(
        create_mcp_router(
            retrieval_pipeline=MagicMock(), retrieve_v2_service=bypass_retrieve_v2_service()
        )
    )
    with TestClient(app) as c:
        yield c


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


def test_tools_list_deep_equals_model_dump(client: TestClient) -> None:
    """tools/list response deep-equals RETRIEVE_DOCUMENTS_TOOL.model_dump(exclude_none=True)."""
    from ragent.routers.mcp_tools.retrieve_documents import RETRIEVE_DOCUMENTS_TOOL

    [tool] = client.post(
        "/mcp/v1", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]
    assert tool == RETRIEVE_DOCUMENTS_TOOL.model_dump(exclude_none=True)


def test_tools_list_advertises_retrieve_tool(client: TestClient) -> None:
    resp = client.post(
        "/mcp/v1",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    [tool] = resp.json()["result"]["tools"]
    assert tool["name"] == "retrieve"
    assert isinstance(tool["description"], str)
    assert "retriev" in tool["description"].lower()


def test_retrieve_input_schema_required_fields(client: TestClient) -> None:
    """query AND document_id_list are both required for the document-scoped tool."""
    [tool] = client.post(
        "/mcp/v1", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]
    schema = tool["inputSchema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"query", "document_id_list"}


def test_retrieve_input_schema_property_types(client: TestClient) -> None:
    """Each property has the documented type + bounds."""
    [tool] = client.post(
        "/mcp/v1", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]
    props = tool["inputSchema"]["properties"]

    assert props["query"]["type"] == "string"
    assert props["query"]["minLength"] == 1

    assert props["document_id_list"]["type"] == "array"
    assert props["document_id_list"]["minItems"] == 1
    assert props["document_id_list"]["maxItems"] == 100

    assert props["top_k"]["type"] == "integer"
    assert props["top_k"]["minimum"] == 1
    assert props["top_k"]["maximum"] == 200
    assert props["top_k"]["default"] == 20

    assert props["min_score"]["type"] == "number"
    assert props["min_score"]["minimum"] == 0


def test_retrieve_optional_fields_have_no_null_default(client: TestClient) -> None:
    """Optional fields must not advertise default:null in inputSchema."""
    [tool] = client.post(
        "/mcp/v1", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]
    props = tool["inputSchema"]["properties"]
    for name in ("min_score",):
        assert props[name].get("default") is not None or "default" not in props[name], (
            f"inputSchema.properties.{name} must not have default:null"
        )


def test_retrieve_input_schema_all_properties_have_descriptions(client: TestClient) -> None:
    """Every inputSchema property must carry a non-empty description."""
    [tool] = client.post(
        "/mcp/v1", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]
    props = tool["inputSchema"]["properties"]
    for name, prop in props.items():
        assert "description" in prop and prop["description"], (
            f"inputSchema.properties.{name} has no description"
        )


def test_retrieve_tool_advertises_output_schema(client: TestClient) -> None:
    """T-MCP13.1 — outputSchema declares the structuredContent.sources contract."""
    [tool] = client.post(
        "/mcp/v1", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]
    schema = tool["outputSchema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["sources"]
    assert schema["additionalProperties"] is False
    items = schema["properties"]["sources"]["items"]
    assert items["additionalProperties"] is False
    assert set(items["properties"]) == {
        "document_id",
        "source_app",
        "source_id",
        "source_meta",
        "type",
        "source_title",
        "source_url",
        "mime_type",
        "excerpt",
        "score",
    }


def test_retrieve_tool_has_readonly_hint(client: TestClient) -> None:
    """Retrieve never writes data — readOnlyHint must be True."""
    [tool] = client.post(
        "/mcp/v1", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]
    assert tool.get("annotations", {}).get("readOnlyHint") is True
