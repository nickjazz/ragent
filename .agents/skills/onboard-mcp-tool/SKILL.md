---
name: onboard-mcp-tool
description: Add or refactor a first-party ragent API as an MCP tool. Use when the user asks to expose an existing ragent API through POST /mcp/v1, add a new first-party MCP tool, adjust MCP tool schemas, or prevent drift between REST Pydantic models and MCP inputSchema contracts.
---

# Onboarding a First-Party MCP Tool

Use this workflow for in-process `/mcp/v1` tools that wrap ragent's own typed APIs.
Do not use it for the standalone MCP Hub; Hub tools are YAML-driven and live under
`src/ragent/mcp_hub/`.

## Core Rules

- The API request model is the source of truth. Do not hand-write a second MCP
  JSON schema for the same fields.
- Put field descriptions, bounds, defaults, and MCP metadata next to the Pydantic
  `Field(...)` declaration.
- MCP may expose a subset of the REST request model, but it must not redefine
  field types, bounds, defaults, or descriptions.
- Use `json_schema_extra` metadata for MCP projection:
  - `x-mcp-exposed: false` hides a field from MCP.
  - `x-mcp-enum-env: ENV_VAR_NAME` adds an env-driven enum. If the env var is
    empty or unset, hide that field from MCP.
- Strip every `x-mcp-*` key before returning `tools/list`.
- Keep `additionalProperties: false` on MCP input schemas.
- Prefer read-only tools. Any mutating tool needs an explicit safety, auth, and
  confirmation strategy before implementation.

## Implementation Steps

1. Locate the REST Pydantic request model. Move it to `src/ragent/schemas/` if
   it currently lives inside a router and another MCP module needs to import it.
2. Add or update `Field(description=..., json_schema_extra=...)` on the request
   model. Keep descriptions useful to an agent, not tied to storage internals.
3. Register the tool with the first-party MCP registry. A tool spec must define:
   `name`, `description`, `request_model`, optional `annotations`, and a handler.
4. Let the registry validate arguments in two phases:
   - projected MCP JSON Schema first, rejecting hidden or unknown fields;
   - Pydantic `model_validate()` second, applying shared defaults and validators.
5. Keep JSON-RPC transport concerns in `src/ragent/routers/mcp.py`; tool-specific
   behavior belongs in the tool module.
6. Update `.env.example`, deployment config, env-var docs, MCP spec docs, and
   API examples when the MCP public contract changes.

## Required Tests

- `POST /mcp/v1` `tools/list` returns the tool with the projected schema.
- Hidden fields are absent from `tools/list` and rejected through `tools/call`.
- Env-driven enum fields appear only when the env var is non-empty.
- Enum values outside the allow-list return `MCP_TOOL_INPUT_INVALID`.
- Omitted optional arguments use the Pydantic request model defaults.
- The underlying REST endpoint remains unaffected by MCP-only projection rules.
- The tool result shape is covered through the `/mcp/v1` endpoint, not only helper tests.
