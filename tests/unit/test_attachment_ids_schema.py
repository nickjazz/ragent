"""T-CAT.14 — RunAgentInput accepts attachment_ids."""

from twp_ai.schemas import Attachment, RunAgentInput


def test_attachment_schema_creation():
    """Attachment type can be created."""
    att = Attachment(
        attachment_id="att_1",
        filename="test.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
    )
    assert att.attachment_id == "att_1"
    assert att.filename == "test.pdf"


def test_run_agent_input_accepts_attachment_ids():
    """RunAgentInput accepts optional attachment_ids field."""
    body = RunAgentInput(
        thread_id="t1",
        run_id="r1",
        parent_run_id=None,
        messages=[],
        tools=[],
        state={},
        context=[],
        forwarded_props={},
        attachment_ids=["att_1", "att_2"],
    )
    assert body.attachment_ids == ["att_1", "att_2"]


def test_run_agent_input_attachment_ids_optional():
    """RunAgentInput attachment_ids is optional."""
    body = RunAgentInput(
        thread_id="t1",
        run_id="r1",
        parent_run_id=None,
        messages=[],
        tools=[],
        state={},
        context=[],
        forwarded_props={},
    )
    assert body.attachment_ids is None
