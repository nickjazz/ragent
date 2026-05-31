"""Agent Protocol — the single extension point for twp-ai.

An Agent receives a RunAgentInput, runs whatever logic it needs
(direct LLM call, LangGraph graph, CrewAI crew, etc.), and yields
SSE-formatted event strings directly.

To add a new agent type, implement this Protocol:

    class MyAgent:
        def run(self, request: RunAgentInput, model: str) -> Generator[str, None, None]:
            yield to_sse(RunStartedEvent(run_id=new_id()))
            # ... your logic ...
            yield to_sse(RunFinishedEvent(run_id=run_id))

The agent owns:
  - Its own conversation flow (turns, loops, branching)
  - Which events to emit and when
  - Error handling

twp-ai provides building blocks (compose.py) but does not enforce flow.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Protocol

from .schemas import RunAgentInput


class Agent(Protocol):
    def run(
        self,
        request: RunAgentInput,
        model: str,
    ) -> Generator[str, None, None]: ...
