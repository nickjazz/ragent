"""BrainAgent — proxies a twp-ai run to the ragent-brain service.

The brain speaks twp-ai SSE natively (POST /run → twp-ai event stream), so this
is a thin pass-through: forward the RunAgentInput as-is (skill/context already
injected by the router), relay each SSE frame back. Contrast ADKCaller/ADKAgent,
which translate a legacy upstream wire shape into twp-ai events.

Implements the twp-ai `Agent` protocol (structural): `run(request, model) ->
Generator[str]` yielding SSE-formatted strings. `user_id`/`user_token` are
per-request, so each instance is scoped to one run (like ADKCaller).
"""

from __future__ import annotations

from collections.abc import Generator

import httpx
import structlog
from twp_ai.events import RunErrorEvent, to_sse
from twp_ai.schemas import RunAgentInput

logger = structlog.get_logger(__name__)

_HTTPX_ERRORS = (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError)
_SSE_DATA_PREFIX = "data:"


class BrainAgent:
    """twp-ai pass-through backend for the ragent-brain service."""

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        brain_url: str,
        user_id: str,
        user_token: str,
        brain_key: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._http = http_client
        self._url = brain_url.rstrip("/") + "/run"
        self._user_id = user_id
        self._user_token = user_token
        self._brain_key = brain_key
        self._timeout = timeout

    def run(self, request: RunAgentInput, model: str) -> Generator[str, None, None]:
        # by_alias → camelCase wire shape the brain's twp-ai schema expects.
        payload = request.model_dump(by_alias=True, exclude_none=True)
        if model and not payload.get("model"):
            payload["model"] = model
        headers = {"X-User-Id": self._user_id, "Content-Type": "application/json"}
        if self._user_token:
            headers["Authorization"] = self._user_token
        if self._brain_key:
            headers["X-Brain-Key"] = self._brain_key
        try:
            with self._http.stream(
                "POST", self._url, json=payload, headers=headers, timeout=self._timeout
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    # The brain emits standard SSE (each event is a `data: {json}`
                    # line + blank line). Re-emit each data line as a complete SSE
                    # frame; the router tees these into Redis / relays to the client.
                    if line.startswith(_SSE_DATA_PREFIX):
                        yield line + "\n\n"
        except _HTTPX_ERRORS as exc:
            logger.warning(
                "brain.upstream_error",
                error_type=type(exc).__name__,
                brain_url=self._url,
            )
            yield to_sse(
                RunErrorEvent(
                    message="brain upstream request failed",
                    code="BRAIN_UPSTREAM_ERROR",
                    run_id=request.run_id,
                    thread_id=request.thread_id or "",
                )
            )
