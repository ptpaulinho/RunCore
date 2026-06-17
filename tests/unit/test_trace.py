"""Unit tests for trace module."""
import json
import tempfile
import os

import pytest

from runcore.trace.collector import TraceCollector
from runcore.trace.cost import calculate_llm_cost, calculate_total_cost
from runcore.trace.storage import save_trace, load_trace


def test_llm_call_logs_tokens_and_cost():
    collector = TraceCollector()
    run_id = collector.start_run("test_agent", "test task")
    llm_call = collector.record_llm_call(run_id, "claude-3-5-sonnet-20241022", 500, 200, 800.0)
    assert llm_call.prompt_tokens == 500
    assert llm_call.completion_tokens == 200
    assert llm_call.cost > 0
    assert llm_call.latency_ms == 800.0
    collector.end_run(run_id, success=True)


def test_tool_call_logs_arguments_and_result():
    collector = TraceCollector()
    run_id = collector.start_run("test_agent", "test task")
    result = {"invoice_id": "INV-1", "amount": 99.99}
    tc = collector.record_tool_call(run_id, "get_invoice", {"invoice_id": "INV-1"}, result, True, 150.0)
    assert tc.name == "get_invoice"
    assert tc.arguments == {"invoice_id": "INV-1"}
    assert tc.result == result
    assert tc.success is True
    assert tc.latency_ms == 150.0
    collector.end_run(run_id, success=True)


def test_trace_saved_and_reloaded():
    collector = TraceCollector()
    run_id = collector.start_run("agent_x", "save test")
    collector.record_llm_call(run_id, "gpt-4", 100, 50, 300.0)
    collector.record_tool_call(run_id, "search", {"q": "foo"}, ["bar"], True, 100.0)
    collector.end_run(run_id, success=True, quality_score=0.9)
    trace = collector.get_trace(run_id)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        save_trace(trace, path)
        reloaded = load_trace(path)
        assert reloaded.run_id == trace.run_id
        assert reloaded.agent_name == "agent_x"
        assert len(reloaded.llm_calls) == 1
        assert len(reloaded.tool_calls) == 1
        assert reloaded.quality_score == pytest.approx(0.9)
    finally:
        os.unlink(path)


def test_cost_calculation():
    cost = calculate_llm_cost("claude-3-5-sonnet-20241022", 1000, 500)
    assert cost > 0
    # $0.003/1k input + $0.015/1k output = $0.003 + $0.0075 = $0.0105
    assert cost == pytest.approx(0.0105, rel=0.01)


def test_total_cost_aggregation():
    collector = TraceCollector()
    run_id = collector.start_run("agent", "task")
    collector.record_llm_call(run_id, "claude-3-5-sonnet-20241022", 1000, 500, 500.0)
    collector.record_llm_call(run_id, "claude-3-5-sonnet-20241022", 500, 200, 300.0)
    collector.end_run(run_id, success=True)
    trace = collector.get_trace(run_id)
    assert trace.total_cost > 0
    expected = sum(c.cost for c in trace.llm_calls)
    assert trace.total_cost == pytest.approx(expected, rel=0.001)
