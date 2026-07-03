"""Shared SSE test helpers for chatagent v3 tests."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx

from ragent.bootstrap.composition import _build_chatagent_agent_factory
from ragent.repositories.document_repository import DocumentRepository
from ragent.routers.chatagent_v3 import AgentFactory
from ragent.services.retrieve_v2_service import RetrieveV2Service
from ragent.utility.datetime import from_iso


def sse_line(payload: dict) -> str:
    return f"data: {json.dumps(payload)}"


def msg_line(
    content: str | None = None,
    message_id: str = "msg-1",
    role: str = "assistant",
    *,
    agent_type: str | None = None,
    tool_name: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str | None = None,
    hitl: dict | None = None,
) -> str:
    msg: dict = {"role": role, "messageId": message_id}
    if content is not None:
        msg["content"] = content
    if agent_type:
        msg["messageMeta"] = {"langgraph_node": agent_type}
    if tool_name:
        msg["displayMeta"] = {"toolName": tool_name}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if finish_reason:
        msg["finish_reason"] = finish_reason
    if hitl:
        msg["humanInTheLoopMeta"] = hitl
    return sse_line(
        {"returnCode": 96200, "returnMessage": "success", "returnData": {"messages": [msg]}}
    )


def done_line() -> str:
    return "data: [Done]"


def resp_mock(lines: list[str]) -> MagicMock:
    m = MagicMock(spec=httpx.Response)
    m.raise_for_status.return_value = None
    m.iter_lines.return_value = iter(lines)
    return m


def parse_sse_events(text: str) -> list[dict]:
    # A frame may carry an `id:` line (resumable stream) ahead of its `data:`
    # line; pull the data line out of each block regardless.
    events: list[dict] = []
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :].strip()))
    return events


def parse_sse_ids(text: str) -> list[str]:
    return [
        line[len("id: ") :].strip()
        for block in text.strip().split("\n\n")
        for line in block.splitlines()
        if line.startswith("id: ")
    ]


def real_agent_factory(
    http_client: httpx.Client,
    *,
    api_url: str,
    ap_name: str,
    auth: str | None,
    timeout: float = 30.0,
) -> AgentFactory:
    """Build an ``AgentFactory`` that wires the real ``ADKAgent``/``ADKCaller``.

    Mirrors the production wiring in ``composition.py`` so router tests keep
    exercising the actual ADK backend, just constructed via the injected
    factory instead of inline inside the router.
    """

    return _build_chatagent_agent_factory(
        http_client, api_url=api_url, ap_name=ap_name, auth=auth, timeout=timeout
    )


# ---------------------------------------------------------------------------
# Shared datetime helper
# ---------------------------------------------------------------------------


def dt(s: str) -> datetime:
    """Parse an ISO datetime string and attach UTC timezone (for test use)."""
    return from_iso(s)


# ---------------------------------------------------------------------------
# Shared RetrieveV2Service test helpers
# ---------------------------------------------------------------------------


def make_doc_row(document_id: str, create_user: str) -> SimpleNamespace:
    """Minimal document namespace for RetrieveV2Service ownership tests."""
    return SimpleNamespace(document_id=document_id, create_user=create_user)


def bypass_retrieve_v2_service() -> RetrieveV2Service:
    """RetrieveV2Service stub that always passes ownership checks (for MCP router tests)."""
    svc = MagicMock(spec=RetrieveV2Service)
    svc.assert_owner = AsyncMock(return_value=None)
    return svc


def make_retrieve_v2_service(rows: dict) -> RetrieveV2Service:
    """Build a RetrieveV2Service with a mocked DocumentRepository."""
    repo = AsyncMock(spec=DocumentRepository)
    repo.get_by_document_ids.return_value = rows
    return RetrieveV2Service(document_repo=repo)


def make_retrieval_doc(doc_id: str = "ID1") -> SimpleNamespace:
    """Minimal Haystack doc namespace for retrieval pipeline tests."""
    return SimpleNamespace(
        meta={
            "document_id": doc_id,
            "source_app": "chat_attachment",
            "source_id": "SRC-1",
            "source_meta": "thread-1",
            "source_title": "report.pdf",
            "source_url": None,
            "mime_type": "application/pdf",
            "raw_content": "excerpt text",
        },
        content="excerpt text",
        score=0.9,
    )
