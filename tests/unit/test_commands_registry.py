"""Unit tests for CommandRegistry.dispatch()."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

from twp_ai.schemas import Message

from ragent.commands import CommandRegistry, SlashCommand


def _msg(content: str, role: str = "user") -> Message:
    return Message(id="m1", role=role, content=content)


def _make_cmd(name: str, *, matches: bool) -> SlashCommand:
    cmd = MagicMock(spec=SlashCommand)
    cmd.name = name
    cmd.matches.return_value = matches

    def _gen() -> Generator[str, None, None]:
        yield f"data: {name}\n\n"

    cmd.handle.return_value = _gen()
    return cmd


_DISPATCH_KWARGS = dict(
    user_id="u1",
    auth_header="Bearer tok",
    run_id="r1",
    thread_id="t1",
    jwt_header="X-Auth-Token",
)


def test_dispatch_returns_none_when_no_commands() -> None:
    registry = CommandRegistry()
    result = registry.dispatch([_msg("hello")], **_DISPATCH_KWARGS)
    assert result is None


def test_dispatch_returns_none_when_no_match() -> None:
    registry = CommandRegistry()
    registry.register(_make_cmd("/foo", matches=False))
    result = registry.dispatch([_msg("hello")], **_DISPATCH_KWARGS)
    assert result is None


def test_dispatch_returns_generator_from_first_match() -> None:
    registry = CommandRegistry()
    cmd = _make_cmd("/foo", matches=True)
    registry.register(cmd)
    result = registry.dispatch([_msg("/foo")], **_DISPATCH_KWARGS)
    assert result is not None
    assert list(result) == ["data: /foo\n\n"]
    cmd.handle.assert_called_once_with(**_DISPATCH_KWARGS)


def test_dispatch_first_matching_command_wins() -> None:
    registry = CommandRegistry()
    cmd_a = _make_cmd("/foo", matches=True)
    cmd_b = _make_cmd("/bar", matches=True)
    registry.register(cmd_a)
    registry.register(cmd_b)
    result = registry.dispatch([_msg("/foo")], **_DISPATCH_KWARGS)
    assert result is not None
    list(result)
    cmd_a.handle.assert_called_once()
    cmd_b.handle.assert_not_called()


def test_dispatch_skips_non_matching_commands() -> None:
    registry = CommandRegistry()
    cmd_a = _make_cmd("/foo", matches=False)
    cmd_b = _make_cmd("/bar", matches=True)
    registry.register(cmd_a)
    registry.register(cmd_b)
    result = registry.dispatch([_msg("/bar")], **_DISPATCH_KWARGS)
    assert result is not None
    cmd_a.handle.assert_not_called()
    cmd_b.handle.assert_called_once()
