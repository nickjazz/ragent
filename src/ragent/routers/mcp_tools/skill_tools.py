"""MCP tool descriptors for the skill-management family — `list_skills`,
`get_skill`, `update_skill`, `delete_skill` (T-SK; `skill-manager` preset).

Grouped in one module (rather than one file per tool) because they form a
cohesive CRUD family and share the skill-object schemas. This module is also the
single home for the shared skill schema fragments (`SKILL_WRITE_PROPERTIES`,
`SKILL_BRIEF_SCHEMA`) that `create_skill` reuses, so the two write tools and the
brief-output shape can never drift apart.

All are scoped to the authenticated caller — the MCP router supplies the owner
from `get_user_id`; `user_id` is never a tool argument (every `inputSchema` is
`additionalProperties: false`, so a spoofed `user_id` is rejected).
"""

from __future__ import annotations

from typing import Any

from mcp.types import Tool, ToolAnnotations

from ragent.schemas.skill import DESCRIPTION_MAX, INSTRUCTIONS_MAX, NAME_MAX

_SKILL_ID_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "description": (
        "The target skill's skill_id (from list_skills). If you only know the "
        "skill's name, pass skill_name instead — never ask the user for an id."
    ),
}

_SKILL_NAME_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": NAME_MAX,
    "description": (
        "The skill's current name, matched case-insensitively against the "
        "caller's own skills. Use this when the user refers to a skill by name."
    ),
}

# get/update/delete identify their target by EXACTLY ONE of skill_id |
# skill_name (names are unique per owner, so a name resolves deterministically).
_TARGET_ONE_OF: list[dict[str, Any]] = [
    {"required": ["skill_id"]},
    {"required": ["skill_name"]},
]

# Editable write fields (name/description/instructions/enabled), mirroring
# `schemas.skill.SkillWriteRequest` bounds. Shared by `create_skill` and
# `update_skill` so the two write tools can never drift apart — both spread this
# block but declare their own `required`: `create` requires {name, instructions}
# (description/enabled default for a new row); `update` is a full replace, so it
# requires all five (skill_id + every write field) to avoid silent clobbering.
SKILL_WRITE_PROPERTIES: dict[str, Any] = {
    "name": {
        "type": "string",
        "minLength": 1,
        "maxLength": NAME_MAX,
        "description": "Short, unique label for the skill.",
    },
    "description": {
        "type": "string",
        "maxLength": DESCRIPTION_MAX,
        "description": "One-line summary of what the skill is for.",
    },
    "instructions": {
        "type": "string",
        "minLength": 1,
        "maxLength": INSTRUCTIONS_MAX,
        "description": "The persona / operating instructions the skill applies to a chat turn.",
    },
    "enabled": {
        "type": "boolean",
        "description": "Whether the skill is active (default true).",
    },
}

# Lightweight skill object (no instructions/timestamps) — list_skills items and
# create_skill output. The one authoritative definition of the "brief" shape.
SKILL_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["skill_id", "name", "description", "enabled", "readonly"],
    "properties": {
        "skill_id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "enabled": {"type": "boolean"},
        "readonly": {"type": "boolean"},
    },
}

# Full skill object (brief + instructions/timestamps) — get_skill / update_skill
# output. Built from the brief so the shared fields are declared exactly once.
_SKILL_FULL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [*SKILL_BRIEF_SCHEMA["required"], "instructions", "created_at", "updated_at"],
    "properties": {
        **SKILL_BRIEF_SCHEMA["properties"],
        "instructions": {"type": "string"},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
    },
}

LIST_SKILLS_TOOL = Tool(
    name="list_skills",
    description=(
        "List the current user's skills (skill_id, name, description, enabled, readonly). "
        "Built-in skills have readonly=true. Use this to browse or disambiguate; "
        "get_skill / update_skill / delete_skill also accept the skill's name directly "
        "(skill_name). Returns only the caller's own skills."
    ),
    annotations=ToolAnnotations(readOnlyHint=True),
    inputSchema={"type": "object", "additionalProperties": False, "properties": {}},
    outputSchema={
        "type": "object",
        "additionalProperties": False,
        "required": ["skills"],
        "properties": {"skills": {"type": "array", "items": SKILL_BRIEF_SCHEMA}},
    },
)

GET_SKILL_TOOL = Tool(
    name="get_skill",
    description=(
        "Fetch one of the current user's skills in full (including its instructions), "
        "by skill_id or by its current name (skill_name — case-insensitive). A skill "
        "not owned by the caller is reported as not found."
    ),
    annotations=ToolAnnotations(readOnlyHint=True),
    inputSchema={
        "type": "object",
        "additionalProperties": False,
        "oneOf": _TARGET_ONE_OF,
        "properties": {"skill_id": _SKILL_ID_SCHEMA, "skill_name": _SKILL_NAME_SCHEMA},
    },
    outputSchema={
        "type": "object",
        "additionalProperties": False,
        "required": ["skill"],
        "properties": {"skill": _SKILL_FULL_SCHEMA},
    },
)

UPDATE_SKILL_TOOL = Tool(
    name="update_skill",
    description=(
        "Full-replace one of the current user's skills, targeted by skill_id or by its "
        "current name (skill_name — case-insensitive). This OVERWRITES every field, so "
        "you must supply ALL of name, description, instructions, and enabled — omitting a "
        "field is not a partial edit, it replaces that field. Read the current values with "
        "get_skill first so you don't blank the description or re-enable a disabled skill. "
        "Set enabled=false to hide it. Built-in skills cannot be updated; a name collision "
        "fails the call."
    ),
    annotations=ToolAnnotations(readOnlyHint=False),
    inputSchema={
        "type": "object",
        "additionalProperties": False,
        # Full replace → every write field is required, so an omitted field can never
        # silently default (description→"" / enabled→true) and clobber existing data.
        # The target is exactly one of skill_id | skill_name (oneOf); `name` remains
        # the NEW name to write, distinct from the skill_name lookup key.
        "required": ["name", "description", "instructions", "enabled"],
        "oneOf": _TARGET_ONE_OF,
        "properties": {
            "skill_id": _SKILL_ID_SCHEMA,
            "skill_name": _SKILL_NAME_SCHEMA,
            **SKILL_WRITE_PROPERTIES,
        },
    },
    outputSchema={
        "type": "object",
        "additionalProperties": False,
        "required": ["skill"],
        "properties": {"skill": _SKILL_FULL_SCHEMA},
    },
)

DELETE_SKILL_TOOL = Tool(
    name="delete_skill",
    description=(
        "Permanently delete one of the current user's skills, targeted by skill_id or "
        "by its current name (skill_name — case-insensitive). Built-in skills cannot "
        "be deleted. Confirm with the user before calling."
    ),
    annotations=ToolAnnotations(readOnlyHint=False),
    inputSchema={
        "type": "object",
        "additionalProperties": False,
        "oneOf": _TARGET_ONE_OF,
        "properties": {"skill_id": _SKILL_ID_SCHEMA, "skill_name": _SKILL_NAME_SCHEMA},
    },
    outputSchema={
        "type": "object",
        "additionalProperties": False,
        "required": ["skill_id", "deleted"],
        "properties": {"skill_id": {"type": "string"}, "deleted": {"type": "boolean"}},
    },
)
