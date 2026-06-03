"""T-CAv2.S1 — ChatAgentV2Request schema tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ragent.schemas.chatagent import ChatAgentV2Request


def test_defaults():
    req = ChatAgentV2Request(inputData={"message": "hi"})
    assert req.metadata.session is None
    assert req.stream is False


def test_session_provided():
    req = ChatAgentV2Request(
        metadata={"session": "s1"},
        inputData={"message": "hi"},
    )
    assert req.metadata.session == "s1"


def test_stream_true():
    req = ChatAgentV2Request(inputData={"message": "hi"}, stream=True)
    assert req.stream is True


def test_missing_message_raises():
    with pytest.raises(ValidationError):
        ChatAgentV2Request(inputData={})


def test_extra_field_in_root_raises():
    with pytest.raises(ValidationError):
        ChatAgentV2Request(inputData={"message": "hi"}, unknown_field="x")


def test_extra_field_in_metadata_raises():
    with pytest.raises(ValidationError):
        ChatAgentV2Request(
            metadata={"session": "s1", "apName": "should_not_be_here"},
            inputData={"message": "hi"},
        )


def test_extra_field_in_input_data_raises():
    with pytest.raises(ValidationError):
        ChatAgentV2Request(inputData={"message": "hi", "extra": "x"})
