"""build_injection — cache-safe placement of the felt block on the user turn."""

from feltstate.render import build_injection


def test_block_prefixes_user_message():
    out = build_injection("[how I feel right now]\nmood: calm", "what's next?")
    assert out == "[how I feel right now]\nmood: calm\n\nwhat's next?"
    # the user's words are preserved verbatim, after the block
    assert out.endswith("what's next?")
    assert out.startswith("[how I feel right now]")


def test_empty_block_returns_user_message_unchanged():
    assert build_injection("", "hello") == "hello"
    assert build_injection("   \n  ", "hello") == "hello"  # whitespace-only counts as empty


def test_separated_by_blank_line():
    out = build_injection("FELT", "MSG")
    assert "\n\n" in out
    assert out.index("FELT") < out.index("MSG")  # block first, message after
