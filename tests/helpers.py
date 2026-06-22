"""Shared SSE test helpers for chatagent v3 tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx


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
