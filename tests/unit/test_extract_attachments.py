"""T-CAT.16 — Extract and strip attachments from session history hidden block."""


from ragent.services.chatagent_session import _extract_attachments_from_hidden


def test_extract_attachments_from_hidden_parses_json_array():
    """Extract attachments from <hidden> block."""
    hidden = """<hidden>
<attachments>[{"attachmentId":"att_1","filename":"test.pdf","mimeType":"application/pdf","sizeBytes":1024}]</attachments>
<context>[]</context>
</hidden>

User message"""

    result = _extract_attachments_from_hidden(hidden)
    assert result is not None
    assert len(result) == 1
    assert result[0]["attachmentId"] == "att_1"
    assert result[0]["filename"] == "test.pdf"


def test_extract_attachments_returns_none_when_no_block():
    """Extract returns None when no <attachments> block exists."""
    hidden = """<hidden>
<context>[]</context>
</hidden>

User message"""

    result = _extract_attachments_from_hidden(hidden)
    assert result is None


def test_extract_attachments_handles_multiple():
    """Extract handles multiple attachments."""
    hidden = """<hidden>
<attachments>[{"attachmentId":"att_1","filename":"doc1.pdf"},{"attachmentId":"att_2","filename":"doc2.txt"}]</attachments>
<context>[]</context>
</hidden>"""

    result = _extract_attachments_from_hidden(hidden)
    assert result is not None
    assert len(result) == 2
    assert result[0]["attachmentId"] == "att_1"
    assert result[1]["attachmentId"] == "att_2"


def test_extract_attachments_handles_empty_array():
    """Extract handles empty attachments array."""
    hidden = """<hidden>
<attachments>[]</attachments>
<context>[]</context>
</hidden>"""

    result = _extract_attachments_from_hidden(hidden)
    assert result is None or result == []
