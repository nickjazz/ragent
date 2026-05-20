"""T8.5a — joserfc-verified JWT (§3.5, rewritten 2026-05-20).

``verify_jwt`` verifies the inbound JWT against a JWKS-backed key set using
joserfc (the actively-maintained successor to ``authlib.jose``).
``build_token_manager`` fetches the OIDC discovery document + JWKS once at
composition via an injected ``httpx.Client`` — the client is the seam that
controls SSL verification, custom CA bundles, proxies, and timeouts.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from joserfc import jwt
from joserfc.errors import ExpiredTokenError, JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from ragent.bootstrap.http_logging import install_error_logging
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
    """JWKS-backed JWT verifier built once at composition, reused per request.

    ``jwks`` is the parsed key set (decoder picks the right key by ``kid``);
    ``audience`` and ``expected_iss`` are precomputed for the registry + the
    manual issuer compare. No I/O after construction (§3.5 cache-reuse).
    """

    jwks: KeySet
    audience: str
    expected_iss: str  # already rstripped


def build_token_manager(
    *,
    domain: str,
    audience: str,
    use_https: bool = True,
    verify_ssl: bool = True,
    client: httpx.Client | None = None,
) -> VerifyingTokenManager:
    """Compose the JWKS verifier from OIDC discovery.

    OIDC discovery + JWKS are fetched HERE (at composition time), so a
    misconfigured ``OIDC_DOMAIN`` aborts boot rather than 500-ing the first
    protected request.

    ``client`` is the explicit DI seam: production passes ``None`` and we
    build a ``httpx.Client(verify=verify_ssl, timeout=10.0)`` wired with the
    project's HTTP error-logging hook; tests inject a ``MockTransport``-backed
    client so OIDC + JWKS routes resolve in-process with zero real network.
    When ``client`` is provided, ``verify_ssl`` is ignored — the injected
    client owns SSL policy. ``verify_ssl=False`` (default-built client only)
    is for dev/staging against self-signed Keycloak ONLY — production should
    mount the IdP's CA via ``SSL_CERT_FILE`` instead.
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(verify=verify_ssl, timeout=10.0)
        install_error_logging(client, client_name="oidc")
    try:
        scheme = "https" if use_https else "http"
        oidc_url = f"{scheme}://{domain}/.well-known/openid-configuration"
        oidc_resp = client.get(oidc_url)
        oidc_resp.raise_for_status()
        oidc = oidc_resp.json()
        jwks_resp = client.get(oidc["jwks_uri"])
        jwks_resp.raise_for_status()
        jwks_data = jwks_resp.json()
    finally:
        if own_client:
            client.close()

    return VerifyingTokenManager(
        jwks=KeySet.import_key_set(jwks_data),
        audience=audience,
        expected_iss=str(oidc["issuer"]).rstrip("/"),
    )


def verify_jwt(token: str, *, claim_user_id: str, token_manager: VerifyingTokenManager) -> str:
    """Verify ``token`` against the configured JWKS and return the user-id claim.

    Failure mapping (§4.1.2):
      * empty / malformed / bad-signature / wrong-kid / unsupported-alg →
        ``AUTH_TOKEN_INVALID``
      * expired ``exp`` → ``AUTH_TOKEN_EXPIRED``
      * wrong / missing ``iss`` or ``aud`` / ``nbf`` in future →
        ``AUTH_TOKEN_INVALID``
      * missing or empty ``<claim_user_id>`` → ``AUTH_CLAIM_MISSING``
    """
    if not token:
        raise JwtAuthError(HttpErrorCode.AUTH_TOKEN_INVALID)

    try:
        decoded = jwt.decode(token, token_manager.jwks, algorithms=["RS256"])
    except JoseError as exc:
        raise JwtAuthError(HttpErrorCode.AUTH_TOKEN_INVALID) from exc

    claims = decoded.claims

    # Standard claim validation: exp / nbf / iat / aud via joserfc's registry.
    # iss is checked separately below to absorb trailing-slash variance between
    # OIDC discovery (often `.../`) and real-IdP-issued tokens (often `...`).
    try:
        JWTClaimsRegistry(aud={"essential": True, "value": token_manager.audience}).validate(claims)
    except ExpiredTokenError as exc:
        raise JwtAuthError(HttpErrorCode.AUTH_TOKEN_EXPIRED) from exc
    except JoseError as exc:
        raise JwtAuthError(HttpErrorCode.AUTH_TOKEN_INVALID) from exc

    actual_iss = str(claims.get("iss") or "").rstrip("/")
    if actual_iss != token_manager.expected_iss:
        raise JwtAuthError(HttpErrorCode.AUTH_TOKEN_INVALID)

    user_id = claims.get(claim_user_id)
    if not isinstance(user_id, str) or not user_id:
        raise JwtAuthError(HttpErrorCode.AUTH_CLAIM_MISSING)
    return user_id
