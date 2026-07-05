"""T-ATTACH-R.2b — POST /chatagent/v3/attachments/{attachmentId}/retry router."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragent.routers.attachments import create_attachments_router
from ragent.services.attachment_ingest_service import (
    AttachmentIngestService,
    AttachmentNotFound,
)
from ragent.services.ingest_service import DocumentNotRerunnable

_ATTACHMENT_ID = "DOCAAAAAAAAAAAAAAAAAAAAAA"


def _make_client(svc=None):
    svc = svc or AsyncMock(spec=AttachmentIngestService)
    app = FastAPI()
    app.include_router(create_attachments_router(svc))
    return TestClient(app, raise_server_exceptions=False), svc


# ---------------------------------------------------------------------------
# POST /{attachmentId}/retry — 202 success
# ---------------------------------------------------------------------------


def test_retry_returns_202_with_attachment_id():
    """202 { attachmentId } when ownership passes and status is FAILED."""
    client, svc = _make_client()

    resp = client.post(
        f"/chatagent/v3/attachments/{_ATTACHMENT_ID}/retry",
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 202
    assert resp.json() == {"attachmentId": _ATTACHMENT_ID}
    svc.retry.assert_awaited_once_with(_ATTACHMENT_ID, create_user="alice")


# ---------------------------------------------------------------------------
# POST /{attachmentId}/retry — 403 AUTH_REQUIRED
# ---------------------------------------------------------------------------


def test_retry_returns_403_when_unauthenticated():
    """No X-User-Id header → 403 AUTH_REQUIRED."""
    client, svc = _make_client()

    resp = client.post(f"/chatagent/v3/attachments/{_ATTACHMENT_ID}/retry")

    assert resp.status_code == 403
    assert resp.json()["error_code"] == "AUTH_REQUIRED"
    svc.retry.assert_not_awaited()


# ---------------------------------------------------------------------------
# POST /{attachmentId}/retry — 404 ATTACHMENT_NOT_FOUND
# ---------------------------------------------------------------------------


def test_retry_returns_404_when_attachment_not_found():
    """AttachmentNotFound from service → 404 ATTACHMENT_NOT_FOUND."""
    client, svc = _make_client()
    svc.retry.side_effect = AttachmentNotFound(_ATTACHMENT_ID)

    resp = client.post(
        f"/chatagent/v3/attachments/{_ATTACHMENT_ID}/retry",
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 404
    assert resp.json()["error_code"] == "ATTACHMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /{attachmentId}/retry — 409 ATTACHMENT_NOT_RERUNNABLE
# ---------------------------------------------------------------------------


def test_retry_returns_409_when_not_rerunnable():
    """DocumentNotRerunnable from service → 409 ATTACHMENT_NOT_RERUNNABLE."""
    client, svc = _make_client()
    svc.retry.side_effect = DocumentNotRerunnable(_ATTACHMENT_ID)

    resp = client.post(
        f"/chatagent/v3/attachments/{_ATTACHMENT_ID}/retry",
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 409
    assert resp.json()["error_code"] == "ATTACHMENT_NOT_RERUNNABLE"
