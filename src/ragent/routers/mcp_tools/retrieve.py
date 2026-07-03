"""Shared MCP tool helpers: schema builder and output schema for sources."""

from __future__ import annotations

from typing import Any


def _build_mcp_input_schema(model: type) -> dict[str, Any]:
    """Pydantic v2 emits anyOf:[{type:T,...},{type:null}] for Optional fields;
    MCP clients expect plain Draft-7 {type:T,...}. Collapses the union and
    strips auto-generated title fields."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    props = schema.get("properties", {})
    for name in list(props):
        prop = dict(props[name])
        prop.pop("title", None)
        if "anyOf" in prop:
            non_null = [s for s in prop["anyOf"] if s.get("type") != "null"]
            if len(non_null) == 1:
                merged = {k: v for k, v in prop.items() if k != "anyOf"}
                merged.update(non_null[0])
                # After stripping the null branch, "default": null is
                # contradictory — null is no longer a valid value. Optionality
                # is signalled by absence from "required"; drop the null default
                # so clients don't materialise and submit it.
                if "default" in merged and merged["default"] is None:
                    del merged["default"]
                prop = merged
        props[name] = prop
    schema["properties"] = props
    schema.setdefault("additionalProperties", False)
    return schema


# Matches doc_to_source_entry() output verbatim — every key is always present,
# nullable when the source document lacks the field.
_NULLABLE_STRING: dict[str, Any] = {"type": ["string", "null"]}

RETRIEVE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sources"],
    "properties": {
        "sources": {
            "type": "array",
            "description": (
                "Retrieved sources ordered by descending relevance. "
                "Pass this list to the UI's retrieved-sources panel."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "document_id",
                    "source_app",
                    "source_id",
                    "source_meta",
                    "type",
                    "source_title",
                    "source_url",
                    "mime_type",
                    "excerpt",
                    "score",
                ],
                "properties": {
                    "document_id": _NULLABLE_STRING,
                    "source_app": _NULLABLE_STRING,
                    "source_id": _NULLABLE_STRING,
                    "source_meta": _NULLABLE_STRING,
                    "type": {"type": "string"},
                    "source_title": _NULLABLE_STRING,
                    "source_url": _NULLABLE_STRING,
                    "mime_type": _NULLABLE_STRING,
                    "excerpt": {"type": "string"},
                    "score": {"type": ["number", "null"]},
                },
            },
        },
    },
}

