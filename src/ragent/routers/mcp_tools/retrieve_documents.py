"""MCP tool descriptor for the /mcp/v2 document-scoped `retrieve` tool (spec §3.8.6).

Same tool name as v1's corpus-wide retrieve — the `<attachments>` instruction
block tells the LLM to "use the retrieve tool" — but the input contract is the
zero-trust v2 shape: `document_id_list` is mandatory and every id must belong
to the authenticated caller.
"""

from __future__ import annotations

from mcp.types import Tool, ToolAnnotations
from pydantic import ConfigDict

from ragent.routers.mcp_tools.retrieve import RETRIEVE_OUTPUT_SCHEMA, _build_mcp_input_schema
from ragent.schemas.retrieve import RetrieveV2Request


class _RetrieveDocumentsArgs(RetrieveV2Request):
    """extra=forbid: MCP callers must not send undeclared fields."""

    model_config = ConfigDict(extra="forbid")


def _v2_input_schema() -> dict:
    schema = _build_mcp_input_schema(_RetrieveDocumentsArgs)
    # Pydantic emits minLength for list min_length on some versions; pin the
    # JSON-Schema array keyword explicitly so clients see minItems.
    schema["properties"]["document_id_list"].pop("minLength", None)
    schema["properties"]["document_id_list"]["minItems"] = 1
    return schema


RETRIEVE_DOCUMENTS_TOOL = Tool(
    name="retrieve",
    description=(
        "Retrieve ranked chunks from a SPECIFIC set of documents — pass the documentId "
        "values from the <attachments> block as document_id_list (required, non-empty). "
        "Use this to read the content of files attached to the conversation: runs hybrid "
        "semantic + keyword search scoped strictly to those documents. Results are ordered "
        "by descending relevance; structuredContent.sources is the machine-readable source "
        "list. The text content is a <context>-delimited block with a citation table and "
        "[N] excerpt sections: ground your answer on the excerpts and cite by [N] — do NOT "
        "transcribe the <context> block verbatim into your reply. A recently uploaded file "
        "may still be processing, in which case it yields no chunks yet — say so instead of "
        "guessing its content. Does NOT synthesise an answer."
    ),
    annotations=ToolAnnotations(readOnlyHint=True),
    inputSchema=_v2_input_schema(),
    outputSchema=RETRIEVE_OUTPUT_SCHEMA,
)
