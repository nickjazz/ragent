"""Shared upstream-proxy helpers for the ChatAgent session routes.

`/chatagent/v1` and `/chatagent/v3` expose the same session-management surface
(sessionList + session GET/PUT/DELETE) over the same upstream service; only the
v3 `GET /session` reshapes the response (twp-ai roles + hidden stripped) via a
`transform` callback. The request plumbing — threadpool dispatch, status check,
and the timeout→504 / error→502 mapping — is identical, so it lives here once.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import structlog
from fastapi import Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ragent.errors.codes import HttpErrorCode
from ragent.errors.problem import problem

logger = structlog.get_logger(__name__)


def upstream_error() -> Response:
    return problem(502, HttpErrorCode.CHATAGENT_UPSTREAM_ERROR, "Bad Gateway")


def timeout_error() -> Response:
    return problem(504, HttpErrorCode.CHATAGENT_TIMEOUT, "Gateway Timeout")


async def proxy_get(
    *,
    http_client: httpx.Client,
    url: str,
    params: dict[str, str],
    headers: dict[str, str],
    timeout: float,
    log_prefix: str,
    transform: Callable[[Any], Any] | None = None,
    passthrough_4xx: bool = False,
) -> Response:
    try:
        resp = await run_in_threadpool(
            http_client.get, url, params=params, headers=headers, timeout=timeout
        )
        if passthrough_4xx and 400 <= resp.status_code < 500:
            return Response(
                status_code=resp.status_code,
                content=resp.content,
                media_type="application/json",
            )
        resp.raise_for_status()
        payload = resp.json()
        if transform is not None:
            payload = transform(payload)
    except httpx.TimeoutException:
        logger.warning("chatagent.proxy.timeout", route=log_prefix, http_status=504)
        return timeout_error()
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError, AttributeError, TypeError):
        # AttributeError/TypeError guard the transform against a malformed
        # upstream payload — surface it as 502, not an uncaught 500.
        logger.warning("chatagent.proxy.upstream_error", route=log_prefix, http_status=502)
        return upstream_error()
    return JSONResponse(payload)


async def proxy_write(
    *,
    http_client: httpx.Client,
    method: str,
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout: float,
    log_prefix: str,
    passthrough_4xx: bool = False,
) -> Response:
    """POST/PUT proxy. With `passthrough_4xx`, an upstream 4xx is forwarded
    verbatim (status + JSON body) instead of collapsing to 502 — for upstreams
    whose 4xx are intentional, structured responses the client renders (e.g.
    422 project_limit_reached)."""
    try:
        resp = await run_in_threadpool(
            http_client.request, method, url, json=payload, headers=headers, timeout=timeout
        )
        if passthrough_4xx and 400 <= resp.status_code < 500:
            return Response(
                status_code=resp.status_code,
                content=resp.content,
                media_type="application/json",
            )
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return Response(status_code=resp.status_code)
        return JSONResponse(resp.json())
    except httpx.TimeoutException:
        logger.warning("chatagent.proxy.timeout", route=log_prefix, http_status=504)
        return timeout_error()
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError):
        logger.warning("chatagent.proxy.upstream_error", route=log_prefix, http_status=502)
        return upstream_error()


async def proxy_delete(
    *,
    http_client: httpx.Client,
    url: str,
    params: dict[str, str],
    headers: dict[str, str],
    timeout: float,
    log_prefix: str,
) -> Response:
    """DELETE proxy with the same timeout→504 / error→502 mapping as the
    read/write helpers; upstream status + JSON body pass through."""
    try:
        resp = await run_in_threadpool(
            http_client.delete, url, params=params, headers=headers, timeout=timeout
        )
    except httpx.TimeoutException:
        logger.warning("chatagent.proxy.timeout", route=log_prefix, http_status=504)
        return timeout_error()
    except httpx.RequestError:
        logger.warning("chatagent.proxy.upstream_error", route=log_prefix, http_status=502)
        return upstream_error()
    return Response(
        status_code=resp.status_code,
        content=resp.content,
        media_type="application/json" if resp.content else None,
    )
