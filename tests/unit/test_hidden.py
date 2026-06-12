"""strip_machine_context — remove the machine-context wrapper from content.

The upstream persists every user turn verbatim, including the machine-supplied
context the frontend prepended: a `<hidden>…</hidden>` block (v3) or a legacy
bare `<context>…</context>` block (v1). Both must be stripped from the session
history surfaced back to the client.
"""

from ragent.utility.hidden import strip_machine_context


def test_strips_hidden_prefix_block_and_separator() -> None:
    text = "<hidden>\n<context>[]</context>\n<state>{}</state>\n</hidden>\n\nWhat is X?"
    assert strip_machine_context(text) == "What is X?"


def test_strips_legacy_bare_context_block() -> None:
    # Sessions created before v3 wrapped page context in a bare <context> block.
    text = "<context>\n# Page\nsome markdown\n</context>\n\nWhat is X?"
    assert strip_machine_context(text) == "What is X?"


def test_leading_whitespace_around_block_is_removed() -> None:
    # The wrapper may sit behind upstream-added blank lines; the rendered turn
    # must not start with them.
    assert strip_machine_context("\n\n<hidden>x</hidden>\n\nWhat is X?") == "What is X?"


def test_surrounding_whitespace_is_trimmed_internal_preserved() -> None:
    # Session content is whole messages — trim the edges, keep internal blanks.
    assert strip_machine_context("Hello ") == "Hello"
    assert strip_machine_context("\n\nplain") == "plain"
    assert strip_machine_context("line 1\n\nline 2") == "line 1\n\nline 2"


def test_bare_hidden_block_becomes_empty() -> None:
    assert strip_machine_context("<hidden>\n<state>{}</state>\n</hidden>") == ""


def test_whitespace_and_attribute_tag_variants_are_stripped() -> None:
    assert strip_machine_context('<hidden attr="1">x</hidden >\n\ntail') == "tail"
    assert strip_machine_context('<context id="x">y</context >\n\ntail') == "tail"


def test_multiline_block_is_stripped() -> None:
    text = "<hidden>\nline 1\nline 2\n</hidden>\n\nanswer"
    assert strip_machine_context(text) == "answer"


def test_multiple_blocks_all_stripped() -> None:
    assert strip_machine_context("<hidden>a</hidden>mid<context>b</context>end") == "midend"


def test_opening_tag_pins_its_own_closing_tag() -> None:
    # A <hidden> block whose body nests <context> is consumed whole, not from
    # the inner <context>.
    text = "<hidden><context>x</context><state>y</state></hidden>\n\nQ"
    assert strip_machine_context(text) == "Q"
