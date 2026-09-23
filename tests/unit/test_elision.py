"""Unit tests for adaptive stale-tool-output elision (no LLM calls)."""
from runcore.providers.base import Message
from benchmarks.agents.base import _elide_stale_tool_outputs, _ELIDED_STUB


def _tool(content):
    return Message(role="tool", content=content, tool_call_id="x")


def test_below_threshold_untouched():
    msgs = [Message(role="user", content="hi"), _tool("small result")]
    saved = _elide_stale_tool_outputs(msgs, keep_last=1, min_context_tokens=1200)
    assert saved == 0
    assert msgs[1].content == "small result"


def test_above_threshold_elides_old_keeps_recent():
    big = "x" * 2000  # ~500 tokens each
    msgs = [
        Message(role="user", content="task"),
        _tool(big + "A"),
        _tool(big + "B"),
        _tool(big + "C"),
    ]
    saved = _elide_stale_tool_outputs(msgs, keep_last=1, min_context_tokens=1000)
    assert saved > 0
    # Oldest two elided, most recent kept verbatim
    assert msgs[1].content == _ELIDED_STUB
    assert msgs[2].content == _ELIDED_STUB
    assert msgs[3].content == big + "C"


def test_idempotent():
    big = "y" * 2000
    msgs = [Message(role="user", content="t"), _tool(big), _tool(big)]
    s1 = _elide_stale_tool_outputs(msgs, keep_last=1, min_context_tokens=500)
    s2 = _elide_stale_tool_outputs(msgs, keep_last=1, min_context_tokens=500)
    assert s1 > 0 and s2 == 0  # second pass finds nothing new to elide


def test_keep_last_zero_elides_all():
    big = "z" * 2000
    msgs = [_tool(big), _tool(big)]
    _elide_stale_tool_outputs(msgs, keep_last=0, min_context_tokens=100)
    assert all(m.content == _ELIDED_STUB for m in msgs)
