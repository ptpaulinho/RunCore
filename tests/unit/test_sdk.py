"""Tests for the Universal SDK — capture(), instrument(), auto_instrument()."""
from __future__ import annotations

import threading
import time

import pytest

import runcore
from runcore.sdk.capture import Capture
from runcore.sdk import context as _ctx
from runcore.sdk.instrument import instrument, instrument_object
from runcore.sdk.proxy import patch_all, unpatch_all, is_patched
from runcore.atir.spec import ATIRTrace


# ---------------------------------------------------------------------------
# capture() context manager
# ---------------------------------------------------------------------------

def test_capture_creates_atir_trace():
    with runcore.capture("test_agent", task="do something") as cap:
        cap.record_llm(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            input_tokens=400,
            output_tokens=80,
            cost_usd=0.00015,
            duration_ms=320.0,
        )
        cap.record_tool("get_invoice", {"id": "INV-1"}, {"amount": 99}, True, 12.0)

    trace = cap.get_atir()
    assert isinstance(trace, ATIRTrace)
    assert trace.agent_name == "test_agent"
    assert trace.task == "do something"
    assert trace.aggregates.llm_calls == 1
    assert trace.aggregates.tool_calls == 1


def test_capture_aggregates_tokens():
    with runcore.capture("agent") as cap:
        cap.record_llm("openai", "gpt-4o", 300, 60, 0.001, 500.0)
        cap.record_llm("openai", "gpt-4o", 200, 40, 0.0007, 400.0)

    trace = cap.get_atir()
    agg = trace.aggregates
    assert agg.input_tokens == 500
    assert agg.output_tokens == 100
    assert agg.total_tokens == 600
    assert agg.llm_calls == 2


def test_capture_success_defaults_true():
    with runcore.capture("agent") as cap:
        pass
    assert cap._success is True


def test_capture_marks_failure_on_exception():
    with pytest.raises(ValueError):
        with runcore.capture("agent") as cap:
            raise ValueError("oops")
    assert cap._success is False


def test_capture_set_quality():
    with runcore.capture("agent") as cap:
        cap.set_quality(0.95)
    trace = cap.get_atir()
    assert trace.quality_score == 0.95


def test_capture_summary():
    with runcore.capture("agent") as cap:
        cap.record_llm("anthropic", "claude-haiku-4-5-20251001", 100, 20, 0.0001, 200.0)
        cap.record_tool("my_tool", {}, {}, True, 5.0)

    s = cap.summary()
    assert s["agent"] == "agent"
    assert s["llm_calls"] == 1
    assert s["tool_calls"] == 1
    assert s["total_tokens"] == 120


def test_capture_get_trace():
    with runcore.capture("agent", task="t") as cap:
        cap.record_llm("anthropic", "claude-haiku-4-5-20251001", 200, 40, 0.00005, 150.0)

    agent_trace = cap.get_trace()
    assert agent_trace.agent_name == "agent"
    assert len(agent_trace.llm_calls) == 1


# ---------------------------------------------------------------------------
# Thread-local context stack
# ---------------------------------------------------------------------------

def test_context_stack_is_thread_local():
    results = {}

    def worker(name):
        with runcore.capture(name) as cap:
            time.sleep(0.01)
            results[name] = _ctx.current()

    threads = [threading.Thread(target=worker, args=(f"agent_{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads completed without crosstalk
    assert len(results) == 4
    # After thread exit, all captures should be popped
    assert _ctx.current() is None


def test_context_stack_nesting():
    with runcore.capture("outer") as outer:
        assert _ctx.current() is outer
        with runcore.capture("inner") as inner:
            assert _ctx.current() is inner
        assert _ctx.current() is outer
    assert _ctx.current() is None


def test_no_active_capture_returns_none():
    assert _ctx.current() is None
    assert _ctx.is_active() is False


# ---------------------------------------------------------------------------
# instrument() decorator
# ---------------------------------------------------------------------------

def test_instrument_decorator():
    @instrument(agent_name="my_agent")
    def run_task():
        return "done"

    result = run_task()
    assert result == "done"
    assert hasattr(run_task, "__runcore_capture__")


def test_instrument_preserves_return_value():
    @instrument
    def compute(x, y):
        return x + y

    assert compute(3, 4) == 7


def test_instrument_sets_failure_on_exception():
    @instrument(agent_name="failing_agent")
    def bad_fn():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        bad_fn()


def test_instrument_object():
    class FakeAgent:
        def run(self, task):
            return f"ran: {task}"

    agent = FakeAgent()
    instrument_object(agent, method_name="run", agent_name="fake_agent")
    result = agent.run("test task")
    assert result == "ran: test task"


# ---------------------------------------------------------------------------
# auto_instrument() / uninstrument() — no real Anthropic/OpenAI installed
# ---------------------------------------------------------------------------

def test_auto_instrument_returns_dict():
    result = runcore.auto_instrument()
    assert isinstance(result, dict)
    # anthropic/openai may or may not be installed, but keys should be present
    # (returns False if not installed, True if patched)


def test_uninstrument_does_not_raise():
    runcore.auto_instrument()
    runcore.uninstrument()  # should not raise even if nothing was patched


def test_is_patched_returns_dict():
    status = is_patched()
    assert "anthropic" in status
    assert "openai" in status


# ---------------------------------------------------------------------------
# Duplicate tool call detection
# ---------------------------------------------------------------------------

def test_duplicate_tool_calls_counted():
    with runcore.capture("agent") as cap:
        cap.record_tool("search", {"q": "hello"}, {}, True, 5.0)
        cap.record_tool("search", {"q": "hello"}, {}, True, 5.0)  # duplicate

    trace = cap.get_atir()
    assert trace.aggregates.duplicate_tool_calls == 1


def test_no_false_positive_duplicates():
    with runcore.capture("agent") as cap:
        cap.record_tool("search", {"q": "hello"}, {}, True, 5.0)
        cap.record_tool("search", {"q": "world"}, {}, True, 5.0)  # different args

    trace = cap.get_atir()
    assert trace.aggregates.duplicate_tool_calls == 0


# ---------------------------------------------------------------------------
# runcore module top-level API
# ---------------------------------------------------------------------------

def test_module_exports_capture():
    assert callable(runcore.capture)


def test_module_exports_instrument():
    assert callable(runcore.instrument)


def test_module_exports_auto_instrument():
    assert callable(runcore.auto_instrument)


def test_module_exports_uninstrument():
    assert callable(runcore.uninstrument)


def test_module_exports_atir_types():
    from runcore import ATIRTrace, LLMSpan, ToolSpan, ATIRAggregates
    assert ATIRTrace is not None
