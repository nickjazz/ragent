import json
from collections.abc import Generator
from typing import Any

from fastapi.testclient import TestClient

from twp_ai.agents.direct import DirectLLMAgent
from twp_ai.app import create_app
from twp_ai.schemas import RunAgentInput


class FakeCaller:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict], list[Any], str]] = []

    def stream_events(
        self,
        messages: list[dict],
        tools: list[Any],
        model: str,
    ) -> Generator[tuple[str, Any], None, None]:
        self.calls.append((messages.copy(), tools, model))
        if len(self.calls) == 1:
            yield (
                "tool_call",
                {
                    "id": "call_1",
                    "name": "fill_form",
                    "arguments": '{"description":"Better copy"}',
                },
            )
            return
        yield ("text", "Filled the description.")


def _events(lines: list[str]) -> list[dict]:
    return [json.loads(line.removeprefix("data: ").strip()) for line in lines]


def _run_input() -> dict:
    return {
        "threadId": "thread_1",
        "runId": "run_1",
        "state": {"page": {"title": "Edit product"}},
        "messages": [
            {"id": "msg_user_1", "role": "user", "content": "Fill the description"}
        ],
        "tools": [
            {
                "name": "fill_form",
                "description": "Use only when the user asks to edit the form.",
                "parameters": {
                    "type": "object",
                    "properties": {"description": {"type": "string"}},
                },
            }
        ],
        "context": [
            {
                "description": "Current page",
                "value": '{"title":"Edit product","fields":["description"]}',
            }
        ],
        "forwardedProps": {"source": "test"},
    }


def test_run_agent_input_accepts_twp_ai_tool_shape() -> None:
    request = RunAgentInput.model_validate(_run_input())

    assert request.thread_id == "thread_1"
    assert request.run_id == "run_1"
    assert request.messages[0].id == "msg_user_1"
    assert request.tools[0].name == "fill_form"
    assert request.tools[0].parameters["type"] == "object"
    assert request.context[0].description == "Current page"
    assert request.forwarded_props == {"source": "test"}


def test_direct_agent_emits_twp_ai_tool_lifecycle_events() -> None:
    caller = FakeCaller()
    request = RunAgentInput.model_validate(_run_input())

    events = _events(list(DirectLLMAgent(caller).run(request, "model-a")))
    event_types = [event["type"] for event in events]

    assert event_types == [
        "RUN_STARTED",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[0]["threadId"] == "thread_1"
    assert events[0]["runId"] == "run_1"
    assert events[1]["toolCallId"] == "call_1"
    assert events[1]["toolCallName"] == "fill_form"
    assert events[2]["delta"] == '{"description":"Better copy"}'
    assert events[4]["toolCallId"] == "call_1"
    assert events[4]["role"] == "tool"
    assert json.loads(events[4]["content"]) == {"status": "ok"}
    assert events[5]["messageId"] == events[6]["messageId"] == events[7]["messageId"]


def test_direct_agent_sends_tool_result_to_second_llm_turn() -> None:
    caller = FakeCaller()
    request = RunAgentInput.model_validate(_run_input())

    list(DirectLLMAgent(caller).run(request, "model-a"))

    second_turn_messages = caller.calls[1][0]
    assert second_turn_messages[-2]["role"] == "assistant"
    assert second_turn_messages[-2]["tool_calls"][0]["id"] == "call_1"
    assert second_turn_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"status": "ok"}',
    }


def test_run_route_streams_agent_events() -> None:
    class RouteAgent:
        def __init__(self) -> None:
            self.seen_model = ""

        def run(self, request: RunAgentInput, model: str) -> Generator[str, None, None]:
            self.seen_model = model
            yield 'data: {"type":"RUN_STARTED","runId":"run_1","threadId":"thread_1"}\n\n'

    agent = RouteAgent()
    client = TestClient(create_app(agent, default_model="model-default"))

    response = client.post("/run", json=_run_input())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"RUN_STARTED"' in response.text
    assert agent.seen_model == "model-default"
