"""MCP tool descriptors for `POST /mcp/v1` (§3.8).

Each sub-module defines one tool: its input model, inputSchema, and mcp.types.Tool
descriptor. To add a new tool: create a sub-module here, import its Tool descriptor
in mcp.py, and append it to _ALL_TOOLS — the dispatcher needs no logic change.
"""
