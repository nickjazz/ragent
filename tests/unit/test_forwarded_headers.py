"""Forwarded headers: middleware capture + get_forwarded_headers dep.

ragent stays the verification boundary; it *additionally* carries an allowlisted
set of inbound headers (e.g. the raw JWT) through to the brain callers so brain
can relay them to on-behalf-of downstreams. Only allowlisted headers are carried.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from ragent.auth.deps import get_forwarded_headers
from ragent.bootstrap.app import _x_user_id_middleware
from ragent.bootstrap.auth_mode import AuthMode


def _build_app(forward_headers: list[str]) -> FastAPI:
    app = FastAPI()
    _x_user_id_middleware(
        app,
        auth_mode=AuthMode.none,
        forward_headers=forward_headers,
    )

    @app.get("/echo")
    def echo(forwarded: Annotated[dict, Depends(get_forwarded_headers)] = None) -> dict:
        return {"forwarded": forwarded}

    return app


def test_allowlisted_inbound_header_is_captured() -> None:
    with TestClient(_build_app(["X-Auth-Token"])) as client:
        resp = client.get("/echo", headers={"X-Auth-Token": "jwt-abc", "X-Other": "no"})
    assert resp.json()["forwarded"] == {"X-Auth-Token": "jwt-abc"}


def test_non_allowlisted_header_is_not_captured() -> None:
    with TestClient(_build_app(["X-Auth-Token"])) as client:
        resp = client.get("/echo", headers={"X-Other": "no"})
    assert resp.json()["forwarded"] == {}


def test_protected_service_headers_never_enter_the_bag() -> None:
    # even if an operator allowlists a service header (any casing), it must not
    # be captured — it could otherwise collide with the caller-set X-User-Id.
    with TestClient(_build_app(["x-user-id", "X-Brain-Key", "X-Auth-Token"])) as client:
        resp = client.get(
            "/echo",
            headers={"x-user-id": "mallory", "X-Brain-Key": "forged", "X-Auth-Token": "ok"},
        )
    assert resp.json()["forwarded"] == {"X-Auth-Token": "ok"}


def test_empty_allowlist_captures_nothing() -> None:
    with TestClient(_build_app([])) as client:
        resp = client.get("/echo", headers={"X-Auth-Token": "jwt-abc"})
    assert resp.json()["forwarded"] == {}


def test_dep_defaults_to_empty_without_middleware() -> None:
    """A router mounted under a bare app (unit tests) must not KeyError."""
    app = FastAPI()

    @app.get("/echo")
    def echo(forwarded: Annotated[dict, Depends(get_forwarded_headers)] = None) -> dict:
        return {"forwarded": forwarded}

    with TestClient(app) as client:
        assert client.get("/echo").json()["forwarded"] == {}
