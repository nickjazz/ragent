"""T8.2a — Armasec-verified JWT (§3.5, rewritten 2026-05-20).

Replaces the decode-only T8.2 implementation. ``verify_jwt`` calls Armasec's
``TokenManager.extract_token_payload`` which performs JWKS signature
verification + ``aud`` + ``exp`` checks against the configured OIDC provider.
Armasec does not enforce ``iss`` out of the box, so we layer that check on
top. ``build_token_manager`` is the single seam to Armasec internals so
the rest of the codebase imports nothing from ``armasec.*``.
"""

from __future__ import annotations

from dataclasses import dataclass

from armasec.exceptions import AuthenticationError
from armasec.openid_config_loader import OpenidConfigLoader
from armasec.token_decoder import TokenDecoder
from armasec.token_manager import TokenManager
from starlette.datastructures import Headers

from ragent.errors.codes import HttpErrorCode


@dataclass
class JwtAuthError(Exception):
    """Raised by :func:`verify_jwt` on any verification or claim failure.

    Surfaces via the global problem-details handler. Not ``frozen`` — Python's
    exception machinery assigns ``__traceback__`` during ``raise ... from``.
    """

    error_code: HttpErrorCode
    http_status: int = 401

    def __str__(self) -> str:
        return self.error_code


@dataclass(frozen=True)
class VerifyingTokenManager:
    """Wraps an Armasec ``TokenManager`` with the precomputed expected issuer.

    Built once at composition; reused across every protected request. The
    issuer normalization (``rstrip("/")``) is paid once at construction
    instead of per-request.
    """

    manager: TokenManager
    expected_iss: str


def build_token_manager(
    *, domain: str, audience: str, use_https: bool = True
) -> VerifyingTokenManager:
    """Compose Armasec's pieces into a ``VerifyingTokenManager``.

    OIDC discovery + JWKS are fetched HERE (at composition time), NOT lazily on
    first request — a misconfigured ``ARMASEC_DOMAIN`` aborts boot rather than
    500-ing the first protected request. The fetched JWKS is then cached on
    the underlying decoder for the manager's lifetime; subsequent verifications
    do no I/O (§3.5 cache-reuse contract).
    """
    loader = OpenidConfigLoader(domain, use_https=use_https)
    decoder = TokenDecoder(loader.jwks)  # fetches JWKS now, caches on decoder
    manager = TokenManager(loader.config, decoder, audience=audience)
    expected_iss = str(loader.config.issuer).rstrip("/")
    return VerifyingTokenManager(manager=manager, expected_iss=expected_iss)


def verify_jwt(
    token: str, *, claim_user_id: str, token_manager: VerifyingTokenManager
) -> str:
    """Verify ``token`` via Armasec and return the configured user-id claim.

    The middleware passes a raw JWT (no ``Bearer `` prefix — see §3.5);
    Armasec's ``extract_token_payload`` expects an Authorization-style header
    value, so we re-prefix ``Bearer `` here before handing it over.
    """
    if not token:
        raise JwtAuthError(HttpErrorCode.AUTH_TOKEN_INVALID)

    headers = Headers({"Authorization": f"Bearer {token}"})
    try:
        payload = token_manager.manager.extract_token_payload(headers)
    except AuthenticationError as exc:
        # Armasec wraps PyJWT/python-jose errors. The "expired" leaf is the only
        # one we map to a distinct error code per §4.1.2; everything else
        # (bad sig, wrong aud, nbf in future, unknown kid, unsupported
        # alg, malformed token) collapses into AUTH_TOKEN_INVALID.
        if "expired" in str(exc).lower():
            raise JwtAuthError(HttpErrorCode.AUTH_TOKEN_EXPIRED) from exc
        raise JwtAuthError(HttpErrorCode.AUTH_TOKEN_INVALID) from exc

    # Armasec verifies signature + aud + exp via python-jose, but does NOT
    # verify iss out of the box (no `issuer=` kwarg in its decode call). §3.5
    # requires iss == OIDC issuer URL; expected value is precomputed (with
    # trailing slashes stripped to absorb pydantic AnyHttpUrl / IdP variance)
    # at composition time.
    actual_iss = str((payload.model_extra or {}).get("iss") or "").rstrip("/")
    if actual_iss != token_manager.expected_iss:
        raise JwtAuthError(HttpErrorCode.AUTH_TOKEN_INVALID)

    # TokenPayload uses pydantic ``extra="allow"`` so custom claims like
    # ``preferred_username`` / ``email`` land in ``model_extra``. Standard
    # claims (``sub``, ``client_id``) are attributes.
    user_id = getattr(payload, claim_user_id, None)
    if user_id is None and payload.model_extra:
        user_id = payload.model_extra.get(claim_user_id)
    if not isinstance(user_id, str) or not user_id:
        raise JwtAuthError(HttpErrorCode.AUTH_CLAIM_MISSING)
    return user_id
