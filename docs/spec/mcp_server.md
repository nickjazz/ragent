### 3.8 MCP Tool Server (P2.5)

Exposes ragent's retrieval pipeline as a Model Context Protocol tool so external
LLM agents can call the corpus through the MCP standard. The MCP server wraps the
same retrieval contract as `POST /retrieve/v1`; it does not call the LLM. The
calling agent's own LLM performs answer synthesis.

**Decision (B47):** P2.5 implements a real MCP server speaking JSON-RPC 2.0. The
old P1 `POST /mcp/v1/tools/rag` 501 endpoint is removed and replaced by
`POST /mcp/v1` carrying JSON-RPC envelopes.

#### 3.8.1 Protocol

- **Transport:** Streamable HTTP request/response subset. P2.5 supports POST
  only and does not emit server-initiated SSE.
- **Endpoint:** `POST /mcp/v1`.
- **Pinned MCP spec revision:** `"2024-11-05"`.
- **Envelope:** JSON-RPC 2.0.

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
```

Successful responses use the same `id` and place payloads under `result`.
Failures use the same `id` and place JSON-RPC errors under `error`. Parse errors
use `id: null`.

- **Notifications:** Requests without `id` receive no JSON-RPC response. P2.5
  supports `notifications/initialized`.
- **Auth:** governed by `RAGENT_AUTH_MODE`. Auth runs before JSON-RPC dispatch.
  Auth failures return HTTP 401 `application/problem+json`, not a JSON-RPC error.
- **Stateless mode:** P2.5 does not issue or require `Mcp-Session-Id`.
- **Request body cap:** `MCP_REQUEST_MAX_BYTES` (default 256 KiB). Over-limit
  requests return HTTP 413 `application/problem+json`.
- **Batch requests:** not implemented. JSON array bodies return `-32600`.

#### 3.8.2 Supported Methods

| Method | Direction | Purpose |
|---|---|---|
| `initialize` | client to server | Capability negotiation. Returns `{protocolVersion, capabilities, serverInfo}`. |
| `notifications/initialized` | client to server notification | Client signals init complete. Server silently accepts. |
| `tools/list` | client to server | Returns `{tools: [{name, description, annotations?, inputSchema}]}`. |
| `tools/call` | client to server | Invokes a tool. Returns `{content: [{type, text}], isError}`. |
| `ping` | bidirectional | Returns `{}`. |

Any other method returns JSON-RPC `-32601 Method not found`.

#### 3.8.3 The `retrieve` Tool

The sole built-in tool advertised by `tools/list`.

The tool schema is projected from the shared `RetrieveRequest` Pydantic model in
`src/ragent/schemas/retrieve.py`. Bounds, defaults, and field descriptions live
on the Pydantic `Field(...)` declarations so REST and MCP cannot drift. MCP-only
projection metadata is also stored there through `json_schema_extra`.

Default `tools/list` schema when
`RAGENT_MCP_RETRIEVE_SOURCE_APP_ALLOWLIST` is unset or empty:

```json
{
  "name": "retrieve",
  "description": "Retrieve relevant document chunks for a query. Returns ranked excerpts without LLM synthesis.",
  "annotations": {"readOnlyHint": true},
  "inputSchema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "query": {
        "type": "string",
        "minLength": 1,
        "description": "Natural-language search query used to retrieve relevant document chunks from the ragent corpus."
      },
      "top_k": {
        "type": "integer",
        "minimum": 1,
        "maximum": 200,
        "default": 20,
        "description": "Maximum number of ranked chunks to return. Use smaller values for concise answers and larger values when broader evidence is needed."
      },
      "dedupe": {
        "type": "boolean",
        "default": false,
        "description": "When true, return at most one chunk per document_id, preserving the highest-ranked chunk from each document."
      }
    },
    "required": ["query"]
  }
}
```

When `RAGENT_MCP_RETRIEVE_SOURCE_APP_ALLOWLIST` is non-empty, `source_app` is
added to the MCP schema with an enum derived from that comma-separated allowlist.
For example:

```json
"source_app": {
  "type": "string",
  "minLength": 1,
  "maxLength": 64,
  "enum": ["confluence", "slack"],
  "description": "Optional exact-match filter for the source application, such as an ingest connector or upstream system name."
}
```

MCP intentionally hides `source_meta` and `min_score`; REST `/retrieve/v1` still
accepts them. Omitted MCP fields use `RetrieveRequest` defaults, so `top_k`
defaults to `RETRIEVAL_TOP_K` and `min_score` defaults to `RETRIEVAL_MIN_SCORE`
even though `min_score` is not exposed as an MCP input.

`additionalProperties: false` makes the schema closed. Unknown arguments and
hidden fields are rejected with `-32602 MCP_TOOL_INPUT_INVALID`. Internal
`x-mcp-*` projection metadata is stripped before returning `tools/list`.

`annotations.readOnlyHint=true` signals that `retrieve` never writes data. Hosts
on MCP versions that do not understand this annotation can ignore it safely.

#### 3.8.4 Tool Result Shape

`tools/call` returns MCP-compliant text content:

```text
Found 2 chunk(s).

[source #1] score=0.95 | source_app=confluence | document_id=abc123 | title=User Manual
<excerpt text up to EXCERPT_MAX_CHARS>
---
[source #2] score=0.82 | source_app=wiki | document_id=def456 | title=Setup Guide
<excerpt text>
---
```

The runtime label is a numbered source label aligned with the chat context
rendering convention, so calling agents can cite chunks with `[1]`, `[2]`, and so
on. Header metadata fields are omitted when null or empty. `source_app`,
`document_id`, and `title` have CR and LF removed before insertion so metadata
cannot inject fake source headers. Empty results return `Found 0 chunk(s).`.

Retrieval pipeline failures return a JSON-RPC error envelope rather than a
successful tool result with `isError: true`.

#### 3.8.5 Error Codes

| Code | Meaning | Origin |
|---|---|---|
| `-32700` | Parse error | Transport |
| `-32600` | Invalid Request | Transport |
| `-32601` | Method not found | Dispatch |
| `-32602` | Invalid params | Dispatch or tool input validation |
| `-32603` | Internal error | Server |
| `-32001` | Tool execution failed | App |

App-level errors carry `data.error_code` matching the existing `HttpErrorCode`
catalog. Example:

```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32001,"message":"retrieval pipeline failed","data":{"error_code":"MCP_TOOL_EXECUTION_FAILED"}}}
```

#### 3.8.6 BDD

- **S58 mcp initialize:** `initialize` returns protocol version
  `"2024-11-05"`, tools capability, and serverInfo `{name:"ragent"}`.
- **S59 mcp tools/list:** returns exactly one built-in tool named `retrieve`
  with the projected schema described above.
- **S60 mcp tools/call retrieve:** given `{name:"retrieve", arguments:{query:"...", top_k:3}}`,
  the server returns text with `Found N chunk(s).`, numbered source labels, and
  `---` dividers. `result.isError` is `false`.
- **S60a mcp tools/call unknown arg:** extra arguments return `-32602` with
  `MCP_TOOL_INPUT_INVALID`.
- **S60b mcp hidden retrieve arg:** `source_meta` and `min_score` return
  `-32602 MCP_TOOL_INPUT_INVALID` when sent through MCP.
- **S60c mcp source_app allowlist:** `source_app` is rejected when the allowlist
  is unset and accepted only for enum values present in the allowlist.
- **S61 mcp method not found:** an unimplemented method returns `-32601`.
- **S62 mcp tools/call invalid name:** an unknown tool name returns `-32602`
  with `MCP_TOOL_NOT_FOUND`.
- **S63 mcp tools/call missing query:** missing `query` returns `-32602` with
  `MCP_TOOL_INPUT_INVALID`.
- **S64 mcp parse error:** malformed JSON returns HTTP 200 with JSON-RPC
  `-32700` and `id:null`.
- **S65 mcp notifications/initialized:** notification requests return HTTP 204
  with an empty body.
- **S66 mcp auth required:** auth failures return HTTP 401
  `application/problem+json`, not JSON-RPC.
- **S67 mcp tool retrieval failure:** retrieval exceptions return JSON-RPC
  `-32001 MCP_TOOL_EXECUTION_FAILED`.
