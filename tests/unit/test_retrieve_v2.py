"""POST /retrieve/v2 — document-scoped retrieval with anti-IDOR ownership check."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ragent.pipelines.retrieve import build_document_id_filter
from ragent.repositories.document_repository import DocumentRepository
from ragent.routers.retrieve_v2 import create_retrieve_v2_router
from ragent.schemas.attachments import ATTACHMENT_SOURCE_APP
from ragent.schemas.retrieve import RetrieveV2Request
from ragent.services.retrieve_v2_service import DocumentForbidden, RetrieveV2Service

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_v2_request_requires_document_id_list():
    with pytest.raises(ValidationError):
        RetrieveV2Request(query="q")


def test_v2_request_rejects_empty_document_id_list():
    with pytest.raises(ValidationError):
        RetrieveV2Request(query="q", document_id_list=[])


def test_v2_request_accepts_query_and_ids():
    req = RetrieveV2Request(query="q", document_id_list=["ID1", "ID2"])
    assert req.document_id_list == ["ID1", "ID2"]
    assert req.top_k >= 1


# ---------------------------------------------------------------------------
# ES filter builder — Haystack `in` operator compiles to bool.filter terms
# ---------------------------------------------------------------------------


def test_build_document_id_filter_uses_in_operator():
    f = build_document_id_filter(["ID1", "ID2"])
    assert f == {"field": "document_id", "operator": "in", "value": ["ID1", "ID2"]}


def test_haystack_in_filter_compiles_to_es_terms_query():
    """The ES adapter must translate the `in` operator into a terms clause —
    the isolation guarantee of /retrieve/v2 rests on this shape."""
    # verified against haystack-elasticsearch (installed version) — the
    # normalization helper is the exact seam ElasticsearchDocumentStore
    # passes `filters` through before querying.
    from haystack_integrations.document_stores.elasticsearch.filters import _normalize_filters

    es_query = _normalize_filters(build_document_id_filter(["ID1", "ID2"]))

    assert es_query == {"bool": {"must": {"terms": {"document_id": ["ID1", "ID2"]}}}}


# ---------------------------------------------------------------------------
# Ownership service (anti-IDOR)
# ---------------------------------------------------------------------------


def _doc(document_id: str, create_user: str):
    return SimpleNamespace(document_id=document_id, create_user=create_user)


def _svc(rows: dict) -> RetrieveV2Service:
    repo = AsyncMock(spec=DocumentRepository)
    repo.get_by_document_ids.return_value = rows
    return RetrieveV2Service(document_repo=repo)


@pytest.mark.asyncio
async def test_assert_owner_passes_when_all_ids_owned():
    svc = _svc({"ID1": _doc("ID1", "alice"), "ID2": _doc("ID2", "alice")})
    await svc.assert_owner("alice", ["ID1", "ID2"])  # no raise


@pytest.mark.asyncio
async def test_assert_owner_rejects_single_foreign_id():
    svc = _svc({"ID1": _doc("ID1", "alice"), "ID2": _doc("ID2", "bob")})
    with pytest.raises(DocumentForbidden):
        await svc.assert_owner("alice", ["ID1", "ID2"])


@pytest.mark.asyncio
async def test_assert_owner_rejects_missing_id_as_forbidden_not_404():
    """Missing ids are indistinguishable from foreign ids — no existence oracle."""
    svc = _svc({"ID1": _doc("ID1", "alice")})
    with pytest.raises(DocumentForbidden):
        await svc.assert_owner("alice", ["ID1", "ID_MISSING"])


@pytest.mark.asyncio
async def test_assert_owner_rejects_unauthenticated_caller():
    svc = _svc({})
    with pytest.raises(DocumentForbidden):
        await svc.assert_owner(None, ["ID1"])


@pytest.mark.asyncio
async def test_assert_owner_rejects_before_touching_repo_when_unauthenticated():
    repo = AsyncMock(spec=DocumentRepository)
    svc = RetrieveV2Service(document_repo=repo)
    with pytest.raises(DocumentForbidden):
        await svc.assert_owner(None, ["ID1"])
    repo.get_by_document_ids.assert_not_awaited()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


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


def _build_app(service: RetrieveV2Service) -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_retrieve_v2_router(retrieval_pipeline=MagicMock(), retrieve_v2_service=service)
    )
    return app


def test_v2_returns_chunks_for_owned_documents(monkeypatch):
    svc = _svc({"ID1": _doc("ID1", "alice")})
    app = _build_app(svc)
    captured: list[dict] = []

    def _run(*_a, **kw):
        captured.append(kw)
        return [_make_doc()]

    monkeypatch.setattr("ragent.routers.retrieve_v2.run_retrieval", _run)

    with TestClient(app) as client:
        resp = client.post(
            "/retrieve/v2",
            json={"query": "q", "document_id_list": ["ID1"]},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 200
    chunks = resp.json()["chunks"]
    assert len(chunks) == 1
    assert chunks[0]["document_id"] == "ID1"
    assert captured[0]["filters"] == {
        "field": "document_id",
        "operator": "in",
        "value": ["ID1"],
    }


def test_v2_returns_403_when_any_id_foreign(monkeypatch):
    svc = _svc({"ID1": _doc("ID1", "alice"), "ID2": _doc("ID2", "bob")})
    app = _build_app(svc)
    run = MagicMock()
    monkeypatch.setattr("ragent.routers.retrieve_v2.run_retrieval", run)

    with TestClient(app) as client:
        resp = client.post(
            "/retrieve/v2",
            json={"query": "q", "document_id_list": ["ID1", "ID2"]},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 403
    assert resp.json()["error_code"] == "DOCUMENT_FORBIDDEN"
    run.assert_not_called()


def test_v2_returns_403_for_unauthenticated_caller(monkeypatch):
    svc = _svc({})
    app = _build_app(svc)
    run = MagicMock()
    monkeypatch.setattr("ragent.routers.retrieve_v2.run_retrieval", run)

    with TestClient(app) as client:
        resp = client.post("/retrieve/v2", json={"query": "q", "document_id_list": ["ID1"]})

    assert resp.status_code == 403
    run.assert_not_called()


def test_v2_rejects_missing_document_id_list_422():
    svc = _svc({})
    app = _build_app(svc)

    with TestClient(app) as client:
        resp = client.post("/retrieve/v2", json={"query": "q"}, headers={"X-User-Id": "alice"})

    assert resp.status_code == 422


def test_v2_rejects_document_id_list_over_100():
    with pytest.raises(ValidationError):
        RetrieveV2Request(query="q", document_id_list=[f"ID{i}" for i in range(101)])


def test_v2_post_filters_out_chunks_with_none_meta(monkeypatch):
    """Chunks with d.meta=None (e.g. from a pipeline branch that omits meta) are dropped."""
    from types import SimpleNamespace

    svc = _svc({"ID1": _doc("ID1", "alice")})
    app = _build_app(svc)

    def _run(*_a, **kw):
        no_meta = SimpleNamespace(meta=None, content="text", score=0.5)
        return [_make_doc("ID1"), no_meta]

    monkeypatch.setattr("ragent.routers.retrieve_v2.run_retrieval", _run)

    with TestClient(app) as client:
        resp = client.post(
            "/retrieve/v2",
            json={"query": "q", "document_id_list": ["ID1"]},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 200
    chunks = resp.json()["chunks"]
    assert len(chunks) == 1
    assert chunks[0]["document_id"] == "ID1"


def test_v2_post_filters_out_cross_owner_chunks_from_feedback_retriever(monkeypatch):
    """Post-filter ensures no chunk whose document_id is outside the requested
    list leaks through (e.g. from the feedback retriever which ignores the
    terms filter)."""
    svc = _svc({"ID1": _doc("ID1", "alice")})
    app = _build_app(svc)

    def _run(*_a, **kw):
        # Simulate the feedback retriever adding a chunk from an unrelated document.
        return [_make_doc("ID1"), _make_doc("ID_OTHER")]

    monkeypatch.setattr("ragent.routers.retrieve_v2.run_retrieval", _run)

    with TestClient(app) as client:
        resp = client.post(
            "/retrieve/v2",
            json={"query": "q", "document_id_list": ["ID1"]},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 200
    chunks = resp.json()["chunks"]
    assert len(chunks) == 1
    assert chunks[0]["document_id"] == "ID1"


# ---------------------------------------------------------------------------
# Joiner helpers
# ---------------------------------------------------------------------------


def test_build_attachment_exclusion_filter():
    from ragent.pipelines.retrieve import build_attachment_exclusion_filter

    f = build_attachment_exclusion_filter()
    assert f == {"field": "source_app", "operator": "!=", "value": ATTACHMENT_SOURCE_APP}


def test_combine_filters_with_none_base():
    from ragent.pipelines.retrieve import build_attachment_exclusion_filter, combine_filters

    result = combine_filters(None, build_attachment_exclusion_filter())
    assert result == {"field": "source_app", "operator": "!=", "value": ATTACHMENT_SOURCE_APP}


def test_combine_filters_with_existing_base():
    from ragent.pipelines.retrieve import (
        build_attachment_exclusion_filter,
        build_es_filters,
        combine_filters,
    )

    base = build_es_filters("my_app", None)
    result = combine_filters(base, build_attachment_exclusion_filter())
    assert result == {
        "operator": "AND",
        "conditions": [
            {"field": "source_app", "operator": "==", "value": "my_app"},
            {"field": "source_app", "operator": "!=", "value": ATTACHMENT_SOURCE_APP},
        ],
    }
