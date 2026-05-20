"""User-id middleware bypass + JWT auth path (C9, T8.2a / §3.5)."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from ragent.bootstrap.app import _PUBLIC_PATHS, _x_user_id_middleware


def _build_app(
    *,
    user_id_header: str = "X-User-Id",
    jwt_header: str = "X-Auth-Token",
    jwt_claim: str = "preferred_username",
    trust_header: bool = True,
    auth_disabled: bool = True,
    token_manager=None,
) -> FastAPI:
    app = FastAPI()
    _x_user_id_middleware(
        app,
        user_id_header=user_id_header,
        jwt_header=jwt_header,
        jwt_claim=jwt_claim,
        trust_header=trust_header,
        auth_disabled=auth_disabled,
        token_manager=token_manager,
    )

    @app.get("/livez")
    def livez() -> dict:
        return {"ok": True}

    @app.get("/readyz")
    def readyz() -> dict:
        return {"ok": True}

    @app.get("/metrics")
    def metrics() -> str:
        return "metrics"

    @app.get("/protected")
    def protected(
        x_user_id: str | None = Header(default=None, alias=user_id_header),
    ) -> dict:
        return {"user_id": x_user_id}

    return app


@pytest.mark.parametrize("path", ["/livez", "/readyz", "/metrics"])
def test_probe_paths_bypass_user_id(path: str) -> None:
    with TestClient(_build_app()) as client:
        assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_swagger_doc_paths_bypass_user_id(path: str) -> None:
    with TestClient(_build_app()) as client:
        assert client.get(path).status_code == 200


def test_protected_path_requires_user_id() -> None:
    with TestClient(_build_app()) as client:
        resp = client.get("/protected")
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "MISSING_USER_ID"


def test_public_paths_includes_docs_and_probes() -> None:
    expected = {"/livez", "/readyz", "/metrics", "/docs", "/redoc", "/openapi.json"}
    assert expected <= _PUBLIC_PATHS


# --- T8.5a JWT path via joserfc (auth_disabled=false, trust_header=false) ---


def test_jwt_mode_extracts_claim_and_injects_user_id_header(oidc_token_manager, make_token) -> None:
    app = _build_app(trust_header=False, auth_disabled=False, token_manager=oidc_token_manager)
    token = make_token(preferred_username="alice")
    with TestClient(app) as client:
        resp = client.get("/protected", headers={"X-Auth-Token": token})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "alice"


def test_jwt_mode_ignores_user_id_header_when_token_present(oidc_token_manager, make_token) -> None:
    app = _build_app(trust_header=False, auth_disabled=False, token_manager=oidc_token_manager)
    token = make_token(preferred_username="alice")
    with TestClient(app) as client:
        resp = client.get("/protected", headers={"X-Auth-Token": token, "X-User-Id": "mallory"})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "alice"


def test_jwt_mode_missing_token_returns_401(oidc_token_manager) -> None:
    app = _build_app(trust_header=False, auth_disabled=False, token_manager=oidc_token_manager)
    with TestClient(app) as client:
        resp = client.get("/protected")
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTH_TOKEN_INVALID"


def test_jwt_mode_expired_returns_401_expired(oidc_token_manager, make_token) -> None:
    app = _build_app(trust_header=False, auth_disabled=False, token_manager=oidc_token_manager)
    token = make_token(preferred_username="alice", exp=int(time.time()) - 1)
    with TestClient(app) as client:
        resp = client.get("/protected", headers={"X-Auth-Token": token})
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTH_TOKEN_EXPIRED"


def test_jwt_mode_missing_claim_returns_401_claim_missing(oidc_token_manager, make_token) -> None:
    app = _build_app(trust_header=False, auth_disabled=False, token_manager=oidc_token_manager)
    token = make_token()  # no preferred_username
    with TestClient(app) as client:
        resp = client.get("/protected", headers={"X-Auth-Token": token})
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTH_CLAIM_MISSING"


def test_trust_header_true_uses_header_directly() -> None:
    app = _build_app(trust_header=True, auth_disabled=False)
    with TestClient(app) as client:
        resp = client.get("/protected", headers={"X-User-Id": "alice"})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "alice"


def test_auth_disabled_uses_header_regardless_of_trust() -> None:
    app = _build_app(trust_header=False, auth_disabled=True)
    with TestClient(app) as client:
        resp = client.get("/protected", headers={"X-User-Id": "alice"})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "alice"


def test_custom_user_id_header_name() -> None:
    app = _build_app(user_id_header="X-Whoami", trust_header=True, auth_disabled=False)
    with TestClient(app) as client:
        resp = client.get("/protected", headers={"X-Whoami": "alice"})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "alice"


def test_custom_jwt_header_and_claim_round_trip(oidc_token_manager, make_token) -> None:
    app = _build_app(
        jwt_header="X-Token",
        jwt_claim="sub",
        trust_header=False,
        auth_disabled=False,
        token_manager=oidc_token_manager,
    )
    # `sub` is a standard JWT claim; the make_token fixture sets it to
    # "test-sub" by default. Override to verify the claim path end-to-end.
    token = make_token(sub="carol")
    with TestClient(app) as client:
        resp = client.get("/protected", headers={"X-Token": token})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "carol"


def test_jwt_mode_without_token_manager_raises() -> None:
    """T8.2a invariant: JWT mode requires a token_manager."""
    with pytest.raises(RuntimeError, match="token_manager"):
        _build_app(trust_header=False, auth_disabled=False, token_manager=None)
