"""T8.1a Red — Armasec-verified JWT contract (§3.5 rewritten 2026-05-20).

`verify_jwt(token, *, claim_user_id, token_manager)` is the verification seam
(supersedes the decode-only `decode_jwt_payload` from T8.2). Every failure path
returns the spec'd error code through ``JwtAuthError(error_code, http_status=401)``.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from ragent.auth.jwt import JwtAuthError, verify_jwt
from ragent.errors.codes import HttpErrorCode


def test_verify_jwt_happy_path_default_claim(armasec_token_manager, make_token) -> None:
    token = make_token(preferred_username="alice")
    user_id = verify_jwt(
        token,
        claim_user_id="preferred_username",
        token_manager=armasec_token_manager,
    )
    assert user_id == "alice"


def test_verify_jwt_custom_claim_override(armasec_token_manager, make_token) -> None:
    token = make_token(email="alice@example.com")
    user_id = verify_jwt(
        token,
        claim_user_id="email",
        token_manager=armasec_token_manager,
    )
    assert user_id == "alice@example.com"


def test_verify_jwt_bad_signature(armasec_token_manager, make_token) -> None:
    token = make_token(preferred_username="alice")
    # Flip the last char of the signature segment — preserves base64url length
    # and alphabet (matches the tamper idiom in tests/unit/test_feedback_token.py).
    head, body, sig = token.split(".")
    tampered = f"{head}.{body}.{sig[:-1]}{'A' if sig[-1] != 'A' else 'B'}"
    with pytest.raises(JwtAuthError) as exc:
        verify_jwt(
            tampered,
            claim_user_id="preferred_username",
            token_manager=armasec_token_manager,
        )
    assert exc.value.error_code == HttpErrorCode.AUTH_TOKEN_INVALID


# Failure-mode matrix — every row builds a token via `make_token(**kwargs)` and
# expects the listed error_code. Bad signature is excluded (post-build tamper —
# stays as its own test above).
_FAILURE_CASES: list[tuple[str, Callable[..., dict], HttpErrorCode]] = [
    # (label, token-kwargs factory, expected error code)
    (
        "wrong_audience",
        lambda: {"preferred_username": "alice", "aud": "https://wrong.api"},
        HttpErrorCode.AUTH_TOKEN_INVALID,
    ),
    (
        "wrong_issuer",
        lambda: {"preferred_username": "alice", "iss": "https://evil.example.com"},
        HttpErrorCode.AUTH_TOKEN_INVALID,
    ),
    (
        "expired",
        lambda: {"preferred_username": "alice", "exp": int(time.time()) - 60},
        HttpErrorCode.AUTH_TOKEN_EXPIRED,
    ),
    (
        "nbf_in_future",
        lambda: {"preferred_username": "alice", "nbf": int(time.time()) + 3600},
        HttpErrorCode.AUTH_TOKEN_INVALID,
    ),
    ("missing_user_id_claim", dict, HttpErrorCode.AUTH_CLAIM_MISSING),
    ("empty_user_id_claim", lambda: {"preferred_username": ""}, HttpErrorCode.AUTH_CLAIM_MISSING),
]


@pytest.mark.parametrize(
    "kwargs_factory, expected",
    [(factory, code) for (_label, factory, code) in _FAILURE_CASES],
    ids=[label for (label, _factory, _code) in _FAILURE_CASES],
)
def test_verify_jwt_failure_modes(
    armasec_token_manager,
    make_token,
    kwargs_factory: Callable[..., dict],
    expected: HttpErrorCode,
) -> None:
    token = make_token(**kwargs_factory())
    with pytest.raises(JwtAuthError) as exc:
        verify_jwt(
            token,
            claim_user_id="preferred_username",
            token_manager=armasec_token_manager,
        )
    assert exc.value.error_code == expected


@pytest.mark.parametrize(
    "raw_token",
    ["", "not.a.real.token"],
    ids=["empty_token", "malformed_token"],
)
def test_verify_jwt_rejects_malformed_input(armasec_token_manager, raw_token: str) -> None:
    with pytest.raises(JwtAuthError) as exc:
        verify_jwt(
            raw_token,
            claim_user_id="preferred_username",
            token_manager=armasec_token_manager,
        )
    assert exc.value.error_code == HttpErrorCode.AUTH_TOKEN_INVALID


def test_jwt_auth_error_default_http_status() -> None:
    """All Armasec auth failures map to 401 (§4.1.2)."""
    err = JwtAuthError(error_code=HttpErrorCode.AUTH_TOKEN_INVALID)
    assert err.http_status == 401
