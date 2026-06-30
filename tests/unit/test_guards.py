"""Tests for runtime guard engine — dedup, loop break, context compression, savings."""
from __future__ import annotations

import pytest

from runcore.sdk.guards import (
    GuardConfig,
    GuardEngine,
    SavingsReport,
    DuplicateToolCallError,
    LoopBreakError,
)
from runcore.sdk.capture import Capture


# ---------------------------------------------------------------------------
# GuardConfig defaults
# ---------------------------------------------------------------------------

def test_guard_config_defaults():
    cfg = GuardConfig()
    assert cfg.dedup_enabled is True
    assert cfg.dedup_scope == "session"
    assert cfg.loop_break_enabled is True
    assert cfg.loop_break_threshold == pytest.approx(0.40)
    assert cfg.loop_break_min_calls == 4
    assert cfg.context_compression_enabled is True
    assert cfg.context_compression_token_threshold == 800


# ---------------------------------------------------------------------------
# Dedup guard — turn scope
# ---------------------------------------------------------------------------

def test_dedup_blocks_second_identical_call():
    engine = GuardEngine(GuardConfig(dedup_scope="turn"))
    engine.check_tool_call("search", {"q": "foo"})  # first — OK
    with pytest.raises(DuplicateToolCallError) as exc_info:
        engine.check_tool_call("search", {"q": "foo"})  # duplicate
    assert exc_info.value.tool_name == "search"
    assert exc_info.value.arguments == {"q": "foo"}


def test_dedup_allows_different_args():
    engine = GuardEngine(GuardConfig(dedup_scope="turn"))
    engine.check_tool_call("search", {"q": "foo"})
    engine.check_tool_call("search", {"q": "bar"})  # different args — OK


def test_dedup_allows_different_tool_names():
    engine = GuardEngine(GuardConfig(dedup_scope="turn"))
    engine.check_tool_call("search", {"q": "foo"})
    engine.check_tool_call("fetch", {"q": "foo"})  # different name — OK


def test_dedup_reset_on_new_turn():
    engine = GuardEngine(GuardConfig(dedup_scope="turn"))
    engine.check_tool_call("search", {"q": "foo"})
    engine.new_turn()
    engine.check_tool_call("search", {"q": "foo"})  # new turn — should be OK


def test_dedup_session_scope_persists_across_turns():
    engine = GuardEngine(GuardConfig(dedup_scope="session"))
    engine.check_tool_call("search", {"q": "foo"})
    engine.new_turn()
    with pytest.raises(DuplicateToolCallError):
        engine.check_tool_call("search", {"q": "foo"})  # session scope — still blocked


def test_dedup_disabled():
    engine = GuardEngine(GuardConfig(dedup_enabled=False))
    engine.check_tool_call("search", {"q": "foo"})
    engine.check_tool_call("search", {"q": "foo"})  # disabled — no raise


# ---------------------------------------------------------------------------
# Dedup savings tracking
# ---------------------------------------------------------------------------

def test_dedup_savings_accumulate():
    engine = GuardEngine(GuardConfig())
    engine.check_tool_call("a", {"x": 1})
    try:
        engine.check_tool_call("a", {"x": 1})
    except DuplicateToolCallError:
        pass
    try:
        engine.check_tool_call("a", {"x": 1})
    except DuplicateToolCallError:
        pass
    assert engine.savings.blocked_tool_calls == 2
    assert engine.savings.blocked_tool_calls_tokens > 0
    assert engine.savings.blocked_tool_calls_cost_usd > 0


# ---------------------------------------------------------------------------
# Loop break guard
# ---------------------------------------------------------------------------

def test_loop_break_raises_when_risk_high():
    cfg = GuardConfig(loop_break_threshold=0.30, loop_break_min_calls=2)
    engine = GuardEngine(cfg)
    # Simulate reaching min_calls
    for i in range(2):
        engine.check_tool_call(f"tool_{i}", {"i": i})
    with pytest.raises(LoopBreakError) as exc_info:
        engine.check_loop_risk(0.45)
    assert exc_info.value.score == pytest.approx(0.45)
    assert exc_info.value.threshold == pytest.approx(0.30)


def test_loop_break_no_raise_when_low():
    engine = GuardEngine(GuardConfig(loop_break_threshold=0.40, loop_break_min_calls=2))
    for i in range(4):
        engine.check_tool_call(f"t{i}", {"i": i})
    engine.check_loop_risk(0.15)  # below threshold — no raise


def test_loop_break_not_triggered_below_min_calls():
    cfg = GuardConfig(loop_break_threshold=0.10, loop_break_min_calls=10)
    engine = GuardEngine(cfg)
    # Only 2 tool calls — below min_calls
    engine.check_tool_call("a", {"x": 1})
    engine.check_tool_call("b", {"x": 2})
    engine.check_loop_risk(0.90)  # very high but min_calls not reached — no raise


def test_loop_break_disabled():
    engine = GuardEngine(GuardConfig(loop_break_enabled=False, loop_break_min_calls=0))
    engine.check_loop_risk(0.99)  # disabled — no raise


def test_loop_break_savings_tracked():
    cfg = GuardConfig(loop_break_threshold=0.30, loop_break_min_calls=2)
    engine = GuardEngine(cfg)
    for i in range(4):
        engine.check_tool_call(f"t{i}", {"i": i})
    try:
        engine.check_loop_risk(0.50)
    except LoopBreakError:
        pass
    assert engine.savings.loop_breaks == 1


# ---------------------------------------------------------------------------
# Context compression guard
# ---------------------------------------------------------------------------

def test_compression_returns_original_when_disabled():
    engine = GuardEngine(GuardConfig(context_compression_enabled=False))
    msgs = [{"role": "user", "content": "hello"}]
    result = engine.maybe_compress(msgs, 2000)
    assert result is msgs  # same object — no copy


def test_compression_skips_when_below_threshold():
    engine = GuardEngine(GuardConfig(context_compression_token_threshold=1000))
    msgs = [{"role": "user", "content": "short"}]
    result = engine.maybe_compress(msgs, 500)  # below threshold
    assert result is msgs


def test_compression_runs_when_above_threshold():
    engine = GuardEngine(GuardConfig(context_compression_token_threshold=100))
    # Build a message list large enough to actually trigger compression
    msgs = [
        {"role": "user", "content": "Tell me about optimization " * 30},
        {"role": "assistant", "content": "Optimization is the process " * 30},
        {"role": "user", "content": "Tell me about optimization " * 30},  # near-dup
    ]
    result = engine.maybe_compress(msgs, 900)
    # Should return a list (may or may not have compressed, but never crashes)
    assert isinstance(result, list)


def test_compression_never_crashes_on_bad_input():
    engine = GuardEngine(GuardConfig(context_compression_token_threshold=0))
    # Malformed messages — guard should swallow the error
    result = engine.maybe_compress([{"bad": "format"}], 999)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# SavingsReport
# ---------------------------------------------------------------------------

def test_savings_report_totals():
    r = SavingsReport(
        blocked_tool_calls=3,
        blocked_tool_calls_tokens=450,
        blocked_tool_calls_cost_usd=0.00135,
        compression_runs=2,
        tokens_saved_compression=200,
        cost_saved_compression_usd=0.0006,
    )
    assert r.total_tokens_saved == 650
    assert r.total_cost_saved_usd == pytest.approx(0.00195)


def test_savings_report_to_dict():
    r = SavingsReport(blocked_tool_calls=2, blocked_tool_calls_tokens=300,
                      blocked_tool_calls_cost_usd=0.0009)
    d = r.to_dict()
    assert d["blocked_tool_calls"] == 2
    assert d["tokens_saved"] == 300
    assert "cost_saved_usd" in d


def test_savings_report_summary_line_no_savings():
    r = SavingsReport()
    line = r.summary_line()
    assert "No savings" in line


def test_savings_report_summary_line_with_savings():
    r = SavingsReport(blocked_tool_calls=5, blocked_tool_calls_tokens=750,
                      blocked_tool_calls_cost_usd=0.00225)
    line = r.summary_line()
    assert "5 dup calls blocked" in line
    assert "saved" in line


# ---------------------------------------------------------------------------
# Capture integration
# ---------------------------------------------------------------------------

def test_capture_without_guards_no_savings():
    with Capture("agent") as cap:
        cap.record_tool("search", {"q": "a"}, "result", True, 10.0)
        cap.record_tool("search", {"q": "a"}, "result", True, 10.0)
    assert cap.savings_report() is None


def test_capture_with_guards_blocks_dup():
    with Capture("agent", guards=GuardConfig()) as cap:
        cap.record_tool("search", {"q": "a"}, "r", True, 10.0)
        with pytest.raises(DuplicateToolCallError):
            cap.record_tool("search", {"q": "a"}, "r", True, 10.0)


def test_capture_savings_in_summary():
    with Capture("agent", guards=GuardConfig()) as cap:
        cap.record_tool("search", {"q": "a"}, "r", True, 10.0)
        try:
            cap.record_tool("search", {"q": "a"}, "r", True, 10.0)
        except DuplicateToolCallError:
            pass
    s = cap.summary()
    assert "savings" in s
    assert s["savings"]["blocked_tool_calls"] == 1


def test_capture_savings_in_atir():
    with Capture("agent", guards=GuardConfig()) as cap:
        cap.record_tool("t", {"x": 1}, "r", True, 5.0)
        try:
            cap.record_tool("t", {"x": 1}, "r", True, 5.0)
        except DuplicateToolCallError:
            pass
    atir = cap.get_atir()
    assert atir.savings is not None
    assert atir.savings["blocked_tool_calls"] == 1


def test_capture_guard_config_via_runcore_module():
    import runcore
    with runcore.capture("test", guards=runcore.GuardConfig(dedup_enabled=True)) as cap:
        cap.record_tool("fn", {"k": "v"}, None, True, 5.0)
        with pytest.raises(runcore.DuplicateToolCallError):
            cap.record_tool("fn", {"k": "v"}, None, True, 5.0)
    assert cap.savings_report().blocked_tool_calls == 1


def test_capture_new_turn_resets_dedup():
    with Capture("agent", guards=GuardConfig(dedup_scope="turn")) as cap:
        cap.record_tool("search", {"q": "x"}, "r", True, 5.0)
        cap.new_turn()
        cap.record_tool("search", {"q": "x"}, "r", True, 5.0)  # OK after new_turn
    # No exception means test passes


def test_capture_compress_messages_passthrough_without_guards():
    cap = Capture("agent")
    msgs = [{"role": "user", "content": "hi"}]
    result = cap.compress_messages(msgs, 100)
    assert result is msgs


def test_capture_loop_break_via_capture():
    cfg = GuardConfig(loop_break_threshold=0.20, loop_break_min_calls=2)
    with pytest.raises(LoopBreakError):
        with Capture("agent", guards=cfg) as cap:
            for i in range(4):
                cap.record_tool(f"t{i}", {"i": i}, None, True, 5.0)
            cap.check_loop_risk(0.50)
