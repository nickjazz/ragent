"""Registry and dispatch primitives for first-party MCP tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft7Validator
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ragent.mcp_tools.schema import build_mcp_input_schema


class McpToolNotFound(Exception):
    """Raised when a client asks for a tool that is not registered."""


class McpToolInputInvalid(Exception):
    """Raised when tool arguments fail the MCP projected schema or Pydantic model."""


@dataclass(frozen=True)
class McpToolSpec:
    name: str
    description: str
    request_model: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[dict[str, Any]]]
    annotations: dict[str, Any] = field(default_factory=dict)
    _schema: dict[str, Any] = field(init=False, repr=False, compare=False)
    _validator: Draft7Validator = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        schema = build_mcp_input_schema(self.request_model)
        object.__setattr__(self, "_schema", schema)
        object.__setattr__(self, "_validator", Draft7Validator(schema))

    def as_tool(self) -> dict[str, Any]:
        tool: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema(),
        }
        if self.annotations:
            tool["annotations"] = dict(self.annotations)
        return tool

    def input_schema(self) -> dict[str, Any]:
        return self._schema

    async def call(self, arguments: Any) -> dict[str, Any]:
        model = self._validate(arguments)
        return await self.handler(model)

    def _validate(self, arguments: Any) -> BaseModel:
        try:
            self._validator.validate(arguments)
            return self.request_model.model_validate(arguments)
        except JsonSchemaValidationError as exc:
            raise McpToolInputInvalid(_format_jsonschema_error(exc)) from exc
        except PydanticValidationError as exc:
            raise McpToolInputInvalid(str(exc)) from exc


class McpToolRegistry:
    def __init__(self, specs: list[McpToolSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def list_tools(self) -> list[dict[str, Any]]:
        return [spec.as_tool() for spec in self._specs.values()]

    async def call(self, name: str, arguments: Any) -> dict[str, Any]:
        spec = self._specs.get(name)
        if spec is None:
            raise McpToolNotFound(f"unknown tool: {name!r}")
        return await spec.call(arguments)


def _format_jsonschema_error(exc: JsonSchemaValidationError) -> str:
    location = ".".join(str(p) for p in exc.absolute_path) or "arguments"
    return f"{location}: {exc.message}"
