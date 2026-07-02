"""Attachments endpoints — ingest-backed redesign, wire contract unchanged.

The router now fronts AttachmentIngestService only (no repository seam) and
fails closed (403 AUTH_REQUIRED) for unauthenticated callers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragent.routers.attachments import create_attachments_router
from ragent.services.attachment_ingest_service import AttachmentIngestService, AttachmentView
from ragent.services.ingest_service import FileTooLarge


def _view(**kwargs) -> AttachmentView:
    base = dict(
        attachment_id="att_1",
        filename="test.txt",
        mime_type="text/plain",
        size_bytes=100,
        status="READY",
    )
    base.update(kwargs)
    return AttachmentView(**base)


def _build_test_app_with_mocked_attachments(
    *, max_size_bytes: int | None = None
) -> tuple[FastAPI, dict]:
    service = AsyncMock(spec=AttachmentIngestService)

    app = FastAPI()
    kwargs = {} if max_size_bytes is None else {"max_size_bytes": max_size_bytes}
    app.include_router(create_attachments_router(service=service, **kwargs))

    return app, {"service": service}


def test_attachment_routes_follow_versioning_contract() -> None:
    app, _ = _build_test_app_with_mocked_attachments()

    with TestClient(app) as client:
        resp = client.get("/chatagent/v3/attachments?threadId=test", headers={"X-User-Id": "alice"})
        assert resp.status_code != 404


# ---------------------------------------------------------------------------
# Zero-trust: every endpoint fails closed without an authenticated user
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        (
            "post",
            "/chatagent/v3/attachments/upload",
            {"files": {"file": ("t.txt", b"x")}, "data": {"threadId": "thread-1"}},
        ),
        ("get", "/chatagent/v3/attachments?threadId=thread-1", {}),
        ("get", "/chatagent/v3/attachments/mine", {}),
        ("get", "/chatagent/v3/attachments/att_1", {}),
        ("delete", "/chatagent/v3/attachments/att_1", {}),
    ],
)
def test_unauthenticated_request_is_rejected_403(method, path, kwargs) -> None:
    """No anonymous fallback: an anonymous document could never pass the
    /retrieve/v2 ownership check, so accepting the upload would create dead
    data. Fail closed across the whole attachment surface."""
    app, mocks = _build_test_app_with_mocked_attachments()

    with TestClient(app) as client:
        resp = getattr(client, method)(path, **kwargs)

    assert resp.status_code == 403
    assert resp.json()["error_code"] == "AUTH_REQUIRED"
    mocks["service"].upload.assert_not_called()


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


def test_post_attachments_upload_stores_and_returns_id() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].upload = AsyncMock(return_value="att_test_123")

    with TestClient(app) as client:
        resp = client.post(
            "/chatagent/v3/attachments/upload",
            files={"file": ("test.txt", b"test file content")},
            data={"threadId": "thread-1"},
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code == 202
        assert resp.json()["attachmentId"] == "att_test_123"
        kwargs = mocks["service"].upload.await_args.kwargs
        assert kwargs["thread_id"] == "thread-1"
        assert kwargs["create_user"] == "alice"
        assert kwargs["filename"] == "test.txt"


def test_post_attachments_upload_requires_thread_id() -> None:
    app, _ = _build_test_app_with_mocked_attachments()

    with TestClient(app) as client:
        resp = client.post(
            "/chatagent/v3/attachments/upload",
            files={"file": ("test.txt", b"test")},
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code in (400, 422)


def test_post_attachments_upload_logs_request() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].upload = AsyncMock(return_value="att_test_123")

    with TestClient(app) as client, structlog.testing.capture_logs() as logs:
        resp = client.post(
            "/chatagent/v3/attachments/upload",
            files={"file": ("test.txt", b"test file content")},
            data={"threadId": "thread-1"},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 202
    request_log = next(e for e in logs if e["event"] == "attachments.upload_request")
    assert request_log["thread_id"] == "thread-1"
    assert request_log["filename"] == "test.txt"
    assert request_log["user_id"] == "alice"


def test_post_attachments_upload_logs_rejected_mime() -> None:
    app, _ = _build_test_app_with_mocked_attachments()

    with TestClient(app) as client, structlog.testing.capture_logs() as logs:
        resp = client.post(
            "/chatagent/v3/attachments/upload",
            files={"file": ("test.exe", b"x", "application/x-msdownload")},
            data={"threadId": "thread-1"},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 415
    rejected = next(e for e in logs if e["event"] == "attachments.upload_rejected_mime")
    assert rejected["thread_id"] == "thread-1"
    assert rejected["mime_type"] == "application/x-msdownload"
    assert rejected["log_level"] == "warning"


def test_post_attachments_upload_rejected_mime_returns_problem_details() -> None:
    app, _ = _build_test_app_with_mocked_attachments()

    with TestClient(app) as client:
        resp = client.post(
            "/chatagent/v3/attachments/upload",
            files={"file": ("test.exe", b"x", "application/x-msdownload")},
            data={"threadId": "thread-1"},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 415
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["error_code"] == "ATTACHMENT_MIME_UNSUPPORTED"


def test_post_attachments_upload_falls_back_to_extension_when_content_type_generic() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].upload = AsyncMock(return_value="att_test_123")

    with TestClient(app) as client:
        resp = client.post(
            "/chatagent/v3/attachments/upload",
            files={"file": ("report.pdf", b"%PDF-1.4", "application/octet-stream")},
            data={"threadId": "thread-1"},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 202
    mocks["service"].upload.assert_awaited_once()
    assert mocks["service"].upload.await_args.kwargs["mime_type"].value == "application/pdf"


def test_post_attachments_upload_rejects_oversized_file() -> None:
    app, mocks = _build_test_app_with_mocked_attachments(max_size_bytes=10)

    with TestClient(app) as client:
        resp = client.post(
            "/chatagent/v3/attachments/upload",
            files={"file": ("test.txt", b"this file is way over ten bytes")},
            data={"threadId": "thread-1"},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 413
    assert resp.json()["error_code"] == "ATTACHMENT_TOO_LARGE"
    mocks["service"].upload.assert_not_called()


def test_post_attachments_upload_catches_service_raised_file_too_large() -> None:
    """IngestService's authoritative post-read size check maps to the same 413."""
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].upload = AsyncMock(side_effect=FileTooLarge("32B exceeds limit 10B"))

    with TestClient(app) as client:
        resp = client.post(
            "/chatagent/v3/attachments/upload",
            files={"file": ("test.txt", b"this file is way over ten bytes")},
            data={"threadId": "thread-1"},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 413
    assert resp.json()["error_code"] == "ATTACHMENT_TOO_LARGE"
    mocks["service"].upload.assert_called_once()


def test_post_attachments_upload_logs_rejected_size() -> None:
    app, _ = _build_test_app_with_mocked_attachments(max_size_bytes=10)

    with TestClient(app) as client, structlog.testing.capture_logs() as logs:
        resp = client.post(
            "/chatagent/v3/attachments/upload",
            files={"file": ("test.txt", b"this file is way over ten bytes")},
            data={"threadId": "thread-1"},
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 413
    rejected = next(e for e in logs if e["event"] == "attachments.upload_rejected_size")
    assert rejected["thread_id"] == "thread-1"
    assert rejected["log_level"] == "warning"


# ---------------------------------------------------------------------------
# list / get
# ---------------------------------------------------------------------------


def test_get_attachments_lists_by_thread() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].list_by_thread = AsyncMock(return_value=[_view()])

    with TestClient(app) as client:
        resp = client.get(
            "/chatagent/v3/attachments?threadId=thread-1",
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["attachments"]) == 1
        assert body["attachments"][0] == {
            "attachmentId": "att_1",
            "filename": "test.txt",
            "mimeType": "text/plain",
            "sizeBytes": 100,
            "status": "READY",
            "errorCode": None,
            "errorReason": None,
        }
        mocks["service"].list_by_thread.assert_awaited_once_with("thread-1", create_user="alice")


def test_get_attachments_lists_error_fields_when_failed() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].list_by_thread = AsyncMock(
        return_value=[_view(status="FAILED", error_code="EMBEDDER_ERROR", error_reason="boom")]
    )

    with TestClient(app) as client:
        resp = client.get(
            "/chatagent/v3/attachments?threadId=thread-1",
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code == 200
        body = resp.json()["attachments"][0]
        assert body["errorCode"] == "EMBEDDER_ERROR"
        assert body["errorReason"] == "boom"


def test_get_attachments_logs_list_request() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].list_by_thread = AsyncMock(return_value=[])

    with TestClient(app) as client, structlog.testing.capture_logs() as logs:
        resp = client.get(
            "/chatagent/v3/attachments?threadId=thread-1",
            headers={"X-User-Id": "alice"},
        )

    assert resp.status_code == 200
    request_log = next(e for e in logs if e["event"] == "attachments.list_request")
    assert request_log["thread_id"] == "thread-1"
    assert request_log["user_id"] == "alice"


def test_get_attachments_mine_lists_by_user() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].list_by_user = AsyncMock(return_value=[_view()])

    with TestClient(app) as client:
        resp = client.get(
            "/chatagent/v3/attachments/mine",
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code == 200
        assert resp.json()["attachments"][0]["attachmentId"] == "att_1"
        mocks["service"].list_by_user.assert_awaited_once_with("alice")


def test_get_attachments_mine_is_not_swallowed_by_attachment_id_route() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].list_by_user = AsyncMock(return_value=[])

    with TestClient(app) as client:
        resp = client.get(
            "/chatagent/v3/attachments/mine",
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code == 200
        mocks["service"].get.assert_not_called()


def test_get_attachment_by_id_returns_status_and_error_fields() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].get = AsyncMock(return_value=_view(status="PROCESSING"))

    with TestClient(app) as client:
        resp = client.get(
            "/chatagent/v3/attachments/att_1",
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["attachmentId"] == "att_1"
        assert body["status"] == "PROCESSING"
        assert body["errorCode"] is None
        assert body["errorReason"] is None
        mocks["service"].get.assert_awaited_once_with("att_1", create_user="alice")


def test_get_attachment_by_id_returns_404_when_not_found() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].get = AsyncMock(return_value=None)

    with TestClient(app) as client:
        resp = client.get(
            "/chatagent/v3/attachments/att_missing",
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code == 404
        assert resp.json()["error_code"] == "ATTACHMENT_NOT_FOUND"


def test_get_attachment_by_id_returns_404_when_owned_by_another_user() -> None:
    """A document linked to a different user surfaces as not-found, not leaked."""
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].get = AsyncMock(return_value=None)

    with TestClient(app) as client:
        resp = client.get(
            "/chatagent/v3/attachments/att_1",
            headers={"X-User-Id": "mallory"},
        )

        assert resp.status_code == 404
        mocks["service"].get.assert_awaited_once_with("att_1", create_user="mallory")


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_attachment_returns_204_on_success() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].delete = AsyncMock(return_value=True)

    with TestClient(app) as client:
        resp = client.delete(
            "/chatagent/v3/attachments/att_1",
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code == 204
        mocks["service"].delete.assert_awaited_once_with("att_1", create_user="alice")


def test_delete_attachment_returns_404_when_missing_or_not_owned() -> None:
    app, mocks = _build_test_app_with_mocked_attachments()
    mocks["service"].delete = AsyncMock(return_value=False)

    with TestClient(app) as client:
        resp = client.delete(
            "/chatagent/v3/attachments/att_missing",
            headers={"X-User-Id": "alice"},
        )

        assert resp.status_code == 404
        assert resp.json()["error_code"] == "ATTACHMENT_NOT_FOUND"
