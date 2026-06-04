"""Project Pydantic request models into MCP input schemas."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

from ragent.utility.env import list_env

_MCP_EXPOSED = "x-mcp-exposed"
_MCP_ENUM_ENV = "x-mcp-enum-env"
_MCP_PREFIX = "x-mcp-"


def build_mcp_input_schema(model: type[BaseModel]) -> dict[str, Any]:
    source = model.model_json_schema(mode="validation")
    properties = source.get("properties", {})
    required = source.get("required", [])
    exposed: dict[str, Any] = {}

    for name, raw_property in properties.items():
        if raw_property.get(_MCP_EXPOSED) is False:
            continue
        enum_env = raw_property.get(_MCP_ENUM_ENV)
        prop = _collapse_nullable(deepcopy(raw_property))
        if enum_env:
            values = list_env(enum_env)
            if not values:
                continue
            prop["enum"] = values
        _strip_mcp_extensions(prop)
        exposed[name] = prop

    schema: dict[str, Any] = {
        "type": "object",
        "properties": exposed,
        "required": [name for name in required if name in exposed],
        "additionalProperties": False,
    }
    if "$defs" in source:
        schema["$defs"] = deepcopy(source["$defs"])
        _strip_mcp_extensions(schema["$defs"])
    return schema


def _collapse_nullable(prop: dict[str, Any]) -> dict[str, Any]:
    any_of = prop.get("anyOf")
    if not isinstance(any_of, list):
        return prop
    non_null = [entry for entry in any_of if entry.get("type") != "null"]
    if len(non_null) != 1:
        return prop
    collapsed = dict(non_null[0])
    for key, value in prop.items():
        if key != "anyOf" and key not in collapsed:
            collapsed[key] = value
    if collapsed.get("default") is None:
        collapsed.pop("default", None)
    return collapsed


def _strip_mcp_extensions(value: Any) -> None:
    if isinstance(value, dict):
        for key in list(value):
            if key.startswith(_MCP_PREFIX) or key == "title":
                value.pop(key)
            else:
                _strip_mcp_extensions(value[key])
    elif isinstance(value, list):
        for item in value:
            _strip_mcp_extensions(item)
