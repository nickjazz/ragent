"""Strip the machine-context wrapper blocks from session-history content.

The v3 session read surfaces a clean conversation history, but the upstream
persists every user turn verbatim, including the machine-supplied context the
frontend prepended. Two wrapper forms exist:

- the current v3 path wraps it in a `<hidden>…</hidden>` block (with the
  `<context>`/`<state>` payload nested inside);
- the legacy v1 path prepended a bare `<context>…</context>` block.

Both must be removed so a session created before v3 still renders clean
(backward compatibility). The v3 stream does NOT use this — its deltas are the
agent's own generated output and never carry the block.

The matcher is lenient (whitespace / attribute variants, multi-line bodies,
tag-name case). The result is stripped of surrounding whitespace: the session
read deals in whole messages, so the wrapper's separator — and any leading blank
lines the upstream stored around it — must not survive into the rendered turn.
"""

from __future__ import annotations

import re

# A `<hidden …>…</hidden …>` or legacy bare `<context …>…</context …>` block,
# plus any trailing whitespace (the separator before the user message). The
# named backreference pins the closing tag to the opening one, and `hidden` is
# listed first so a v3 block (which nests `<context>`/`<state>`) is consumed
# whole rather than from its inner `<context>`.
_WRAPPER_BLOCK_RE = re.compile(
    r"<\s*(?P<tag>hidden|context)(?:\s+[^>]*)?>.*?<\s*/\s*(?P=tag)\s*>\s*",
    re.IGNORECASE | re.DOTALL,
)


def strip_machine_context(text: str) -> str:
    return _WRAPPER_BLOCK_RE.sub("", text).strip()
