"""T8.D2 — Single source of truth for the resolved ``user_id`` in route handlers.

``get_user_id`` reads ``request.scope[SCOPE_USER_ID_KEY]`` populated by
``_x_user_id_middleware`` (both trust-header and JWT modes). Routes inject it
via ``Depends(get_user_id)`` and so no longer redeclare ``Header(alias=...)``,
which means:

* Adding a new router cannot "forget" to declare the auth header — the
  active scheme is published once by ``install_openapi`` (T8.D1) and the
  resolved value reaches handlers through this dep regardless.
* Renaming ``RAGENT_USER_ID_HEADER`` / ``RAGENT_JWT_HEADER`` no longer
  drifts away from per-route literals.

The header fallback exists for unit tests that construct a single router
under a bare ``FastAPI()`` (no middleware) — production traffic ALWAYS goes
through ``_x_user_id_middleware`` which populates the scope key first.
"""

from __future__ import annotations

import re

from fastapi import Request

from ragent.middleware.logging import _USER_ID_HEADER, SCOPE_USER_ID_KEY

# Strict identifier charset (letters/digits/@._-, max 64). In trust-header
# mode the id is client-asserted and flows into downstream storage keys and
# SQL scoping — reject anything shaped like a path or injection here, at the
# single choke point every route resolves the user through.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9@._-]{1,64}$")


async def get_user_id(request: Request) -> str | None:
    value = request.scope.get(SCOPE_USER_ID_KEY) or request.headers.get(_USER_ID_HEADER)
    if value and _USER_ID_RE.fullmatch(value):
        return value
    return None
