"""POST /mcp/v2 — JSON-RPC MCP server exposing ONLY the document-scoped retrieve tool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragent.repositories.document_repository import DocumentRepository
from ragent.routers.mcp_v2 import create_mcp_v2_router
from ragent.services.retrieve_v2_service import RetrieveV2Service


def _doc_row(document_id: str, create_user: str):
    return SimpleNamespace(document_id=document_id, create_user=create_user)


def _service(rows: dict) -> RetrieveV2Service:
    repo = AsyncMock(spec=DocumentRepository)
    repo.get_by_document_ids.return_value = rows
    return RetrieveV2Service(document_repo=repo)


def _make_doc(doc_id: str = "ID1"):
    return SimpleNamespace(
        meta={
            "document_id": doc_id,
            "source_app": "chat_attachment",
            "source_id": "SRC-1",
            "source_meta": "thread-1",
            "source_title": "report.pdf",
            "source_url": None,
            "mime_type": "application/pdf",
            "raw_content": "excerpt text",
        },
        content="excerpt text",
        score=0.9,
    )


@pytest.fixture()
def app_factory():
    def _build(rows: dict) -> FastAPI:
        app = FastAPI()
        app.include_router(
            create_mcp_v2_router(retrieval_pipeline=MagicMock(), retrieve_v2_service=_service(rows))
        )
        return app

    return _build


def _rpc(client, method, params=None, req_id=1, headers=None):
    envelope = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        envelope["params"] = params
    return client.post("/mcp/v2", json=envelope, headers=headers or {})


# ---------------------------------------------------------------------------
# tools/list — exactly one tool, document_id_list required
# ---------------------------------------------------------------------------


def test_tools_list_exposes_only_scoped_retrieve(app_factory):
    with TestClient(app_factory({})) as client:
        resp = _rpc(client, "tools/list")

    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["retrieve"]
    schema = tools[0]["inputSchema"]
    assert set(schema["required"]) == {"query", "document_id_list"}
    assert schema["properties"]["document_id_list"]["minItems"] == 1
    assert schema["additionalProperties"] is False


def test_initialize_still_served_by_shared_transport(app_factory):
    with TestClient(app_factory({})) as client:
        resp = _rpc(client, "initialize", {"protocolVersion": "2025-06-18"})

    assert resp.json()["result"]["protocolVersion"] == "2025-06-18"


# ---------------------------------------------------------------------------
# tools/call — happy path + zero-trust denials
# ---------------------------------------------------------------------------


def test_tools_call_returns_chunks_scoped_to_documents(app_factory, monkeypatch):
    captured: list[dict] = []

    def _run(*_a, **kw):
        captured.append(kw)
        return [_make_doc()]

    monkeypatch.setattr("ragent.routers.mcp_v2.run_retrieval", _run)
    app = app_factory({"ID1": _doc_row("ID1", "alice")})

    with TestClient(app) as client:
        resp = _rpc(
            client,
            "tools/call",
            {"name": "retrieve", "arguments": {"query": "q", "document_id_list": ["ID1"]}},
            headers={"X-User-Id": "alice"},
        )

    body = resp.json()
    assert "error" not in body
    result = body["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["sources"][0]["document_id"] == "ID1"
    assert result["content"][0]["text"].startswith("<context>")
    assert captured[0]["filters"] == {
        "field": "document_id",
        "operator": "in",
        "value": ["ID1"],
    }


def test_tools_call_foreign_id_returns_jsonrpc_error_not_500(app_factory, monkeypatch):
    run = MagicMock()
    monkeypatch.setattr("ragent.routers.mcp_v2.run_retrieval", run)
    app = app_factory({"ID1": _doc_row("ID1", "bob")})

    with TestClient(app) as client:
        resp = _rpc(
            client,
            "tools/call",
            {"name": "retrieve", "arguments": {"query": "q", "document_id_list": ["ID1"]}},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 200
    error = resp.json()["error"]
    assert error["code"] == -32002  # TOOL_FORBIDDEN — distinct from INVALID_PARAMS (-32602)
    assert error["data"]["error_code"] == "DOCUMENT_FORBIDDEN"
    run.assert_not_called()


def test_tools_call_without_user_identity_fails_closed(app_factory, monkeypatch):
    run = MagicMock()
    monkeypatch.setattr("ragent.routers.mcp_v2.run_retrieval", run)
    app = app_factory({"ID1": _doc_row("ID1", "alice")})

    with TestClient(app) as client:
        resp = _rpc(
            client,
            "tools/call",
            {"name": "retrieve", "arguments": {"query": "q", "document_id_list": ["ID1"]}},
        )

    error = resp.json()["error"]
    assert error["code"] == -32002  # TOOL_FORBIDDEN
    assert error["data"]["error_code"] == "DOCUMENT_FORBIDDEN"
    run.assert_not_called()


def test_tools_call_rejects_missing_document_id_list(app_factory):
    app = app_factory({})

    with TestClient(app) as client:
        resp = _rpc(
            client,
            "tools/call",
            {"name": "retrieve", "arguments": {"query": "q"}},
            headers={"X-User-Id": "alice"},
        )

    error = resp.json()["error"]
    assert error["code"] == -32602
    assert error["data"]["error_code"] == "MCP_TOOL_INPUT_INVALID"


def test_tools_call_rejects_empty_document_id_list(app_factory):
    app = app_factory({})

    with TestClient(app) as client:
        resp = _rpc(
            client,
            "tools/call",
            {"name": "retrieve", "arguments": {"query": "q", "document_id_list": []}},
            headers={"X-User-Id": "alice"},
        )

    assert resp.json()["error"]["code"] == -32602


def test_tools_call_unknown_tool_rejected(app_factory):
    """v1's corpus-wide tool set (e.g. create_skill) is NOT carried into v2."""
    app = app_factory({})

    with TestClient(app) as client:
        resp = _rpc(
            client,
            "tools/call",
            {"name": "create_skill", "arguments": {"name": "x", "instructions": "y"}},
            headers={"X-User-Id": "alice"},
        )

    error = resp.json()["error"]
    assert error["data"]["error_code"] == "MCP_TOOL_NOT_FOUND"
