"""Unit tests for loop detector."""
import pytest

from runcore.loops.detector import LoopDetector
from runcore.loops.policies import LoopPolicyEngine
from runcore.loops.similarity import compute_call_signature, calls_are_identical
from runcore.core.models import AgentTrace, ToolCall
from runcore.core.enums import LoopPolicy


def _make_trace_with_loops() -> AgentTrace:
    calls = [
        ToolCall(name="get_invoice", arguments={"invoice_id": "INV-1"}, result={"amount": 99}, success=True, latency_ms=100),
        ToolCall(name="get_invoice", arguments={"invoice_id": "INV-1"}, result={"amount": 99}, success=True, latency_ms=100),
        ToolCall(name="get_invoice", arguments={"invoice_id": "INV-1"}, result={"amount": 99}, success=True, latency_ms=100),
        ToolCall(name="refund_order", arguments={"order_id": "INV-1"}, result={"status": "ok"}, success=True, latency_ms=200),
    ]
    return AgentTrace(agent_name="test", task="test loop", tool_calls=calls, success=True)


def _make_trace_no_loops() -> AgentTrace:
    calls = [
        ToolCall(name="get_invoice", arguments={"invoice_id": "INV-1"}, result={}, success=True, latency_ms=100),
        ToolCall(name="get_customer", arguments={"email": "a@b.com"}, result={}, success=True, latency_ms=100),
        ToolCall(name="refund_order", arguments={"order_id": "INV-1"}, result={}, success=True, latency_ms=100),
    ]
    return AgentTrace(agent_name="test", task="no loops", tool_calls=calls, success=True)


def _make_trace_with_errors() -> AgentTrace:
    calls = [
        ToolCall(name="get_invoice", arguments={"invoice_id": "INV-X"}, result=None, success=False, latency_ms=100),
        ToolCall(name="get_invoice", arguments={"invoice_id": "INV-X"}, result=None, success=False, latency_ms=100),
        ToolCall(name="get_invoice", arguments={"invoice_id": "INV-X"}, result=None, success=False, latency_ms=100),
    ]
    return AgentTrace(agent_name="test", task="errors", tool_calls=calls, success=False)


def test_detect_repeated_identical_tool_calls():
    detector = LoopDetector()
    trace = _make_trace_with_loops()
    loops = detector.detect_same_tool_calls(trace)
    assert len(loops) > 0
    assert any(g["count"] >= 3 for g in loops)


def test_detect_repeated_errors():
    detector = LoopDetector()
    trace = _make_trace_with_errors()
    errors = detector.detect_repeated_errors(trace)
    assert len(errors) > 0


def test_no_false_positive_on_valid_repeats():
    detector = LoopDetector()
    trace = _make_trace_no_loops()
    loops = detector.detect_same_tool_calls(trace)
    # All calls are unique — should find no loops
    assert len(loops) == 0


def test_loop_risk_score():
    detector = LoopDetector()
    loopy = _make_trace_with_loops()
    clean = _make_trace_no_loops()
    score_loopy = detector.calculate_loop_risk_score(loopy)
    score_clean = detector.calculate_loop_risk_score(clean)
    assert score_loopy > score_clean
    assert 0.0 <= score_loopy <= 1.0
    assert 0.0 <= score_clean <= 1.0


def test_call_signatures():
    tc1 = ToolCall(name="get_invoice", arguments={"invoice_id": "INV-1"}, result={}, success=True, latency_ms=100)
    tc2 = ToolCall(name="get_invoice", arguments={"invoice_id": "INV-1"}, result={}, success=True, latency_ms=200)
    tc3 = ToolCall(name="get_invoice", arguments={"invoice_id": "INV-2"}, result={}, success=True, latency_ms=100)
    assert calls_are_identical(tc1, tc2)   # same name + args
    assert not calls_are_identical(tc1, tc3)  # different args


def test_policy_deduplication():
    engine = LoopPolicyEngine()
    trace = _make_trace_with_loops()
    original_count = len(trace.tool_calls)
    deduped = engine.apply(trace, LoopPolicy.SKIP_DUPLICATE)
    assert len(deduped.tool_calls) < original_count
