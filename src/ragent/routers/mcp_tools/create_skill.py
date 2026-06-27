"""MCP tool descriptor for `create_skill` (§3.8.3, T-SK).

A WRITE tool: it creates a skill under the **authenticated caller's** account
(the MCP router supplies the owner from `get_user_id`; `user_id` is never a tool
argument). The advertised inputSchema mirrors `schemas.skill.SkillWriteRequest`
bounds; the router validates arguments against that schema at call time.
"""

from __future__ import annotations

from typing import Any

from mcp.types import Tool, ToolAnnotations

from ragent.schemas.skill import DESCRIPTION_MAX, INSTRUCTIONS_MAX, NAME_MAX

CREATE_SKILL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "instructions"],
    "properties": {
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
    },
}

CREATE_SKILL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["skill"],
    "properties": {
        "skill": {
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
        },
    },
}

CREATE_SKILL_TOOL = Tool(
    name="create_skill",
    description=(
        "Save a new reusable skill (instruction preset) under the current user's account. "
        "Use this once the user has confirmed the skill's name, description, and instructions. "
        "The skill is created for the authenticated user only; it cannot be created for anyone "
        "else. structuredContent.skill carries the created skill's id and name. On a name "
        "conflict the call fails — ask the user for a different name and retry."
    ),
    annotations=ToolAnnotations(readOnlyHint=False),
    inputSchema=CREATE_SKILL_INPUT_SCHEMA,
    outputSchema=CREATE_SKILL_OUTPUT_SCHEMA,
)
