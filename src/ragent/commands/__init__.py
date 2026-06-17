"""Slash command registry — single source of truth for /command dispatch."""

from __future__ import annotations

from collections.abc import Generator
from typing import Protocol, runtime_checkable

from twp_ai.schemas import Message


@runtime_checkable
class SlashCommand(Protocol):
    name: str

    def matches(self, messages: list[Message]) -> bool: ...

    def handle(
        self,
        *,
        user_id: str,
        auth_header: str,
        run_id: str,
        thread_id: str,
        jwt_header: str,
    ) -> Generator[str, None, None]: ...


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: list[SlashCommand] = []

    def register(self, command: SlashCommand) -> None:
        self._commands.append(command)

    def dispatch(
        self,
        messages: list[Message],
        *,
        user_id: str,
        auth_header: str,
        run_id: str,
        thread_id: str,
        jwt_header: str,
    ) -> Generator[str, None, None] | None:
        for cmd in self._commands:
            if cmd.matches(messages):
                return cmd.handle(
                    user_id=user_id,
                    auth_header=auth_header,
                    run_id=run_id,
                    thread_id=thread_id,
                    jwt_header=jwt_header,
                )
        return None
