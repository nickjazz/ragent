"""DirectLLMAgent — agent for OpenAI-compatible LLM APIs.

Use this when you have a raw LLM API (like ragent's LLMClient) and want
twp-ai to manage the tool-call loop for you.

If you use a framework like LangGraph or CrewAI, write your own Agent
instead — those frameworks manage the loop internally, so you only need
to convert their output events to twp-ai events.

Flow for a client-side tool scenario:
    RUN_STARTED
    Turn 1: LLM talks and/or calls tools
    TOOL_CALL_START / TOOL_CALL_ARGS / TOOL_CALL_END
    RUN_FINISHED

The frontend executes client-side tools and sends their real results back as
`role="tool"` messages in a continuation run. This agent must not synthesize a
tool result or run a confirmation turn before the frontend has acted.

To support a different scenario with different flow, write a new Agent
class rather than subclassing or modifying this one.
"""

from __future__ import annotations

from collections.abc import Generator

from .._compose import Turn, build_messages, build_system_prompt, build_tool_defs
from ..callers.protocol import LLMCaller
from ..events import (
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    to_sse,
)
from ..schemas import RunAgentInput


class DirectLLMAgent:
    """Calls the LLM directly and manages the tool-call loop.

    Suitable for: ragent LLMClient, any OpenAI-compatible API.
    Not needed for: LangGraph, CrewAI, Pydantic AI (they manage loops internally).
    """

    def __init__(self, caller: LLMCaller) -> None:
        self._caller = caller

    def run(self, request: RunAgentInput, model: str) -> Generator[str, None, None]:
        run_id = request.run_id
        yield to_sse(
            RunStartedEvent(
                run_id=run_id,
                thread_id=request.thread_id,
                parent_run_id=request.parent_run_id,
            )
        )

        try:
            tool_defs = build_tool_defs(request)
            messages = build_messages(request, build_system_prompt(request))

            # Turn 1 — LLM talks and/or calls tools
            turn1 = Turn(self._caller.stream_events(messages, tool_defs, model))
            yield from turn1

            # twp-ai tool lifecycle. The current direct runtime receives
            # accumulated tool calls, so args are emitted as one complete delta.
            for tc in turn1.tool_calls:
                yield to_sse(ToolCallStartEvent(tool_call_id=tc["id"], tool_call_name=tc["name"]))
                if tc["arguments"]:
                    yield to_sse(ToolCallArgsEvent(tool_call_id=tc["id"], delta=tc["arguments"]))
                yield to_sse(ToolCallEndEvent(tool_call_id=tc["id"]))
            yield to_sse(RunFinishedEvent(run_id=run_id, thread_id=request.thread_id))

        except Exception as exc:
            yield to_sse(
                RunErrorEvent(
                    message=str(exc),
                    code=type(exc).__name__,
                    run_id=run_id,
                    thread_id=request.thread_id,
                )
            )
