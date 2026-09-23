"""Tests for the LangChain adapter.

All tests mock langchain-core types so the suite runs without the package
installed.  Tests cover:
  - RunCoreLangChainTracer: context manager, wrap(), callback property
  - RunCoreLangChainCallback: global context forwarding, silent drop when no ctx
  - trace_chain: context manager
  - _RunCoreHandler: LLM/tool/chain hooks, error paths, zero-token skip
  - guards integration
  - async ainvoke path
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Patch langchain-core availability so tests work without the package
# ---------------------------------------------------------------------------

import sys
import types
import importlib

# ---------------------------------------------------------------------------
# Inject minimal fake langchain_core before importing the adapter
# ---------------------------------------------------------------------------

def _install_fake_langchain():
    class _FakeCBH:
        """Stand-in for BaseCallbackHandler — no-op __init__."""
        def __init__(self, *a, **kw):
            pass

    lc_base = types.ModuleType("langchain_core.callbacks.base")
    lc_base.BaseCallbackHandler = _FakeCBH

    lc_outputs = types.ModuleType("langchain_core.outputs")
    lc_outputs.LLMResult = Any

    lc_callbacks = types.ModuleType("langchain_core.callbacks")
    lc_callbacks.base = lc_base

    lc_root = types.ModuleType("langchain_core")
    lc_root.callbacks = lc_callbacks

    for name, mod in [
        ("langchain_core",                lc_root),
        ("langchain_core.callbacks",      lc_callbacks),
        ("langchain_core.callbacks.base", lc_base),
        ("langchain_core.outputs",        lc_outputs),
    ]:
        sys.modules[name] = mod  # override (not setdefault) so reload picks it up

    return _FakeCBH

_FakeCBH = _install_fake_langchain()

# Force-reload the adapter module so it picks up the patched langchain_core
import runcore.sdk.adapters.langchain as _lc_mod
importlib.reload(_lc_mod)

from runcore.sdk.adapters.langchain import (
    RunCoreLangChainTracer,
    RunCoreLangChainCallback,
    _RunCoreHandler,
    trace_chain,
)
from runcore.sdk.guards import GuardConfig
from runcore.atir.spec import ToolSpan, LLMSpan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_spans(trace):
    return [s for s in trace.spans if isinstance(s, ToolSpan)]

def _llm_spans(trace):
    return [s for s in trace.spans if isinstance(s, LLMSpan)]

def _llm_result(model="gpt-4", prompt_tokens=50, completion_tokens=20):
    """Minimal fake LangChain LLMResult."""
    r = MagicMock()
    r.generations = []
    r.llm_output = {
        "model_name": model,
        "token_usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }
    return r

def _fake_runnable(return_value=None):
    r = MagicMock()
    r.invoke.return_value = return_value or {"answer": "42"}
    return r


# ===========================================================================
# _RunCoreHandler
# ===========================================================================

class TestRunCoreHandler:
    def _make_handler(self):
        from runcore.sdk.capture import Capture
        cap = Capture(agent_name="h", task="t")
        cap.__enter__()
        return _RunCoreHandler(cap), cap

    def test_on_llm_end_records_span(self):
        h, cap = self._make_handler()
        rid = uuid4()
        h.on_llm_start({}, ["prompt"], run_id=rid)
        h.on_llm_end(_llm_result("gpt-4", 80, 30), run_id=rid)
        cap.__exit__(None, None, None)
        trace = cap.get_atir()
        spans = _llm_spans(trace)
        assert len(spans) >= 1
        assert spans[0].model == "gpt-4"
        assert spans[0].input_tokens == 80
        assert spans[0].output_tokens == 30

    def test_on_llm_end_zero_tokens_skips_span(self):
        h, cap = self._make_handler()
        rid = uuid4()
        h.on_llm_start({}, [], run_id=rid)
        result = MagicMock()
        result.generations = []
        result.llm_output = {}
        h.on_llm_end(result, run_id=rid)
        cap.__exit__(None, None, None)
        trace = cap.get_atir()
        # No span when tokens are both zero
        assert len(_llm_spans(trace)) == 0

    def test_on_llm_error_marks_failure(self):
        h, cap = self._make_handler()
        rid = uuid4()
        h.on_llm_start({}, [], run_id=rid)
        h.on_llm_error(RuntimeError("API down"), run_id=rid)
        cap.__exit__(None, None, None)
        trace = cap.get_atir()
        assert trace.success is False

    def test_on_tool_end_records_span(self):
        h, cap = self._make_handler()
        rid = uuid4()
        h.on_tool_start({}, "search query", run_id=rid)
        h.on_tool_end("result text", run_id=rid, name="web_search")
        cap.__exit__(None, None, None)
        trace = cap.get_atir()
        names = [s.name for s in _tool_spans(trace)]
        assert "web_search" in names

    def test_on_tool_error_records_failure(self):
        h, cap = self._make_handler()
        rid = uuid4()
        h.on_tool_start({}, "input", run_id=rid)
        h.on_tool_error(RuntimeError("timeout"), run_id=rid, name="fetch_url")
        cap.__exit__(None, None, None)
        trace = cap.get_atir()
        assert any(not s.success for s in _tool_spans(trace))

    def test_on_chain_error_marks_failure(self):
        h, cap = self._make_handler()
        rid = uuid4()
        h.on_chain_start({}, {}, run_id=rid)
        h.on_chain_error(RuntimeError("chain broke"), run_id=rid)
        cap.__exit__(None, None, None)
        trace = cap.get_atir()
        assert trace.success is False

    def test_multiple_llm_calls_accumulated(self):
        h, cap = self._make_handler()
        for i in range(3):
            rid = uuid4()
            h.on_llm_start({}, [], run_id=rid)
            h.on_llm_end(_llm_result("gpt-4", 10, 5), run_id=rid)
        cap.__exit__(None, None, None)
        trace = cap.get_atir()
        assert len(_llm_spans(trace)) == 3


# ===========================================================================
# RunCoreLangChainTracer
# ===========================================================================

class TestRunCoreLangChainTracer:
    def test_context_manager_creates_atir(self):
        tracer = RunCoreLangChainTracer(agent_name="lc_agent", task="qa")
        with tracer:
            pass
        trace = tracer.get_atir()
        assert trace.agent_name == "lc_agent"
        assert trace.framework == "langchain"

    def test_callback_property_inside_ctx(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        with tracer:
            cb = tracer.callback
            assert cb is not None

    def test_callback_property_outside_ctx_raises(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        with pytest.raises(RuntimeError, match="context manager"):
            _ = tracer.callback

    def test_no_capture_before_use_raises(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        with pytest.raises(RuntimeError, match="No active capture"):
            tracer.get_atir()

    def test_llm_span_via_callback(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="qa")
        with tracer:
            rid = uuid4()
            tracer.callback.on_llm_start({}, [], run_id=rid)
            tracer.callback.on_llm_end(_llm_result("claude-3", 100, 40), run_id=rid)
        trace = tracer.get_atir()
        spans = _llm_spans(trace)
        assert len(spans) == 1
        assert spans[0].model == "claude-3"

    def test_tool_span_via_callback(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="qa")
        with tracer:
            rid = uuid4()
            tracer.callback.on_tool_start({}, "q", run_id=rid)
            tracer.callback.on_tool_end("answer", run_id=rid, name="knowledge_base")
        trace = tracer.get_atir()
        assert any(s.name == "knowledge_base" for s in _tool_spans(trace))

    def test_exception_marks_failure(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        with pytest.raises(ValueError):
            with tracer:
                raise ValueError("chain error")
        trace = tracer.get_atir()
        assert trace.success is False

    def test_record_llm_manual(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        with tracer:
            tracer.record_llm("gpt-4o", 50, 25, 0.0005, 200.0)
        trace = tracer.get_atir()
        assert len(_llm_spans(trace)) == 1

    def test_record_tool_manual(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        with tracer:
            tracer.record_tool("calculator", {"a": 1}, 2, success=True, duration_ms=5.0)
        trace = tracer.get_atir()
        assert any(s.name == "calculator" for s in _tool_spans(trace))

    def test_set_quality(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        with tracer:
            tracer.set_quality(0.95)
        trace = tracer.get_atir()
        assert trace.quality_score == pytest.approx(0.95)

    def test_savings_none_without_guards(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        with tracer:
            pass
        assert tracer.savings_report() is None

    def test_with_guards(self):
        guards = GuardConfig(dedup_scope="session")
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t", guards=guards)
        with tracer:
            tracer.record_tool("t1", {}, "r", duration_ms=1.0)
        trace = tracer.get_atir()
        assert trace is not None

    def test_framework_custom_tag(self):
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t", framework="lcel_v2")
        with tracer:
            pass
        assert tracer.get_atir().framework == "lcel_v2"

    # ------------------------------------------------------------------
    # wrap()
    # ------------------------------------------------------------------

    def test_wrap_invoke(self):
        runnable = _fake_runnable({"result": "ok"})
        tracer = RunCoreLangChainTracer(agent_name="lc", task="run")
        wrapped = tracer.wrap(runnable)
        result = wrapped.invoke({"q": "hello"})
        assert result == {"result": "ok"}
        trace = tracer.get_atir()
        assert trace.agent_name == "lc"

    def test_wrap_invoke_injects_callback(self):
        """wrap().invoke() must inject our handler into the config callbacks."""
        injected_callbacks = []

        def capturing_invoke(input, config=None, **kw):
            if config and config.get("callbacks"):
                injected_callbacks.extend(config["callbacks"])
            return {"done": True}

        runnable = MagicMock()
        runnable.invoke.side_effect = capturing_invoke

        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        wrapped = tracer.wrap(runnable)
        wrapped.invoke({})

        assert any(isinstance(cb, _RunCoreHandler) for cb in injected_callbacks)

    def test_wrap_invoke_exception_marks_failure(self):
        runnable = MagicMock()
        runnable.invoke.side_effect = RuntimeError("runnable crashed")
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        wrapped = tracer.wrap(runnable)
        with pytest.raises(RuntimeError, match="runnable crashed"):
            wrapped.invoke({})
        trace = tracer.get_atir()
        assert trace.success is False

    def test_wrap_preserves_existing_callbacks(self):
        """wrap().invoke() must keep any pre-existing callbacks in config."""
        seen = []

        def capturing_invoke(input, config=None, **kw):
            if config:
                seen.extend(config.get("callbacks", []))
            return {}

        runnable = MagicMock()
        runnable.invoke.side_effect = capturing_invoke

        extra_cb = MagicMock()
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        wrapped = tracer.wrap(runnable)
        wrapped.invoke({}, config={"callbacks": [extra_cb]})

        assert extra_cb in seen

    def test_wrap_proxy_passes_other_attrs(self):
        runnable = _fake_runnable()
        runnable.some_attr = "present"
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        wrapped = tracer.wrap(runnable)
        assert wrapped.some_attr == "present"

    @pytest.mark.asyncio
    async def test_wrap_ainvoke(self):
        async def _async_invoke(input, config=None, **kw):
            return {"async": "done"}

        runnable = MagicMock()
        runnable.ainvoke = _async_invoke
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        wrapped = tracer.wrap(runnable)
        result = await wrapped.ainvoke({"q": "async question"})
        assert result == {"async": "done"}
        trace = tracer.get_atir()
        assert trace is not None

    @pytest.mark.asyncio
    async def test_wrap_ainvoke_exception_marks_failure(self):
        async def _failing_invoke(input, config=None, **kw):
            raise RuntimeError("async error")

        runnable = MagicMock()
        runnable.ainvoke = _failing_invoke
        tracer = RunCoreLangChainTracer(agent_name="lc", task="t")
        wrapped = tracer.wrap(runnable)
        with pytest.raises(RuntimeError, match="async error"):
            await wrapped.ainvoke({})
        trace = tracer.get_atir()
        assert trace.success is False


# ===========================================================================
# RunCoreLangChainCallback (Mode 2 — global context)
# ===========================================================================

class TestRunCoreLangChainCallback:
    def test_events_recorded_when_capture_active(self):
        import runcore
        cb = RunCoreLangChainCallback()
        with runcore.capture("ctx_agent", task="ctx_task", framework="langchain") as tracer:
            rid = uuid4()
            cb.on_llm_start({}, [], run_id=rid)
            cb.on_llm_end(_llm_result("gpt-3.5", 30, 10), run_id=rid)
        trace = tracer.get_atir()
        spans = _llm_spans(trace)
        assert len(spans) == 1
        assert spans[0].model == "gpt-3.5"

    def test_tool_event_recorded_in_active_ctx(self):
        import runcore
        cb = RunCoreLangChainCallback()
        with runcore.capture("ctx_agent", task="t") as tracer:
            rid = uuid4()
            cb.on_tool_start({}, "input", run_id=rid)
            cb.on_tool_end("output", run_id=rid, name="retriever")
        trace = tracer.get_atir()
        assert any(s.name == "retriever" for s in _tool_spans(trace))

    def test_events_silently_dropped_without_capture(self):
        cb = RunCoreLangChainCallback()
        rid = uuid4()
        # Should not raise even with no active capture
        cb.on_llm_start({}, [], run_id=rid)
        cb.on_llm_end(_llm_result(), run_id=rid)
        cb.on_tool_start({}, "x", run_id=rid)
        cb.on_tool_end("y", run_id=rid, name="t")
        cb.on_chain_start({}, {}, run_id=rid)
        cb.on_chain_end({}, run_id=rid)

    def test_error_events_dropped_without_capture(self):
        cb = RunCoreLangChainCallback()
        rid = uuid4()
        cb.on_llm_error(RuntimeError("err"), run_id=rid)
        cb.on_tool_error(RuntimeError("err"), run_id=rid)
        cb.on_chain_error(RuntimeError("err"), run_id=rid)

    def test_multiple_nested_captures_use_innermost(self):
        import runcore
        cb = RunCoreLangChainCallback()
        with runcore.capture("outer", task="outer") as outer:
            with runcore.capture("inner", task="inner") as inner:
                rid = uuid4()
                cb.on_llm_start({}, [], run_id=rid)
                cb.on_llm_end(_llm_result("gpt-4", 20, 5), run_id=rid)
        # inner should have the span
        inner_trace = inner.get_atir()
        outer_trace = outer.get_atir()
        assert len(_llm_spans(inner_trace)) == 1
        assert len(_llm_spans(outer_trace)) == 0


# ===========================================================================
# trace_chain context manager
# ===========================================================================

class TestTraceChain:
    def test_basic_usage(self):
        with trace_chain("my_chain", task="test task") as tracer:
            rid = uuid4()
            tracer.callback.on_tool_start({}, "input", run_id=rid)
            tracer.callback.on_tool_end("output", run_id=rid, name="search")
        trace = tracer.get_atir()
        assert trace.agent_name == "my_chain"
        assert any(s.name == "search" for s in _tool_spans(trace))

    def test_exception_marks_failure(self):
        with pytest.raises(KeyError):
            with trace_chain("chain", task="t") as tracer:
                raise KeyError("missing key")
        trace = tracer.get_atir()
        assert trace.success is False

    def test_with_guards(self):
        guards = GuardConfig(dedup_scope="turn")
        with trace_chain("chain", task="t", guards=guards) as tracer:
            pass
        trace = tracer.get_atir()
        assert trace is not None
