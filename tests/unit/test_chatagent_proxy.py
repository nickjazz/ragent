"""Shared chatagent session-proxy helpers — error mapping."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from ragent.routers._chatagent_proxy import proxy_get


async def test_proxy_get_transform_exception_maps_to_502() -> None:
    # A transform raising on a malformed upstream payload must surface as 502,
    # not an uncaught 500.
    http_mock = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"messages": object()}
    http_mock.get.return_value = resp

    def boom(_payload: object) -> object:
        raise TypeError("malformed upstream payload")

    result = await proxy_get(
        http_client=http_mock,
        url="http://upstream/session",
        params={},
        headers={},
        timeout=1.0,
        log_prefix="test.session",
        transform=boom,
    )

    assert result.status_code == 502
