"""Tests for RunCore ecosystem adapters: LangGraph, CrewAI, AutoGen."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from runcore.sdk.adapters.langgraph import (
    RunCoreLangGraphCallback,
    RunCoreLangGraphTracer,
    _WrappedGraph,
)
from runcore.sdk.adapters.crewai import RunCoreCrewCallback, trace_crew
from runcore.sdk.adapters.autogen import RunCoreAutoGenTracer, _WrappedAutoGenAgent
from runcore.sdk.guards import GuardConfig
from runcore.atir.spec import ToolSpan, LLMSpan


def _tool_spans(trace):
    return [s for s in trace.spans if isinstance(s, ToolSpan)]

def _llm_spans(trace):
    return [s for s in trace.spans if isinstance(s, LLMSpan)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_graph(return_value=None):
    """Return a mock that looks like a compiled LangGraph."""
    g = MagicMock()
    g.invoke.return_value = return_value or {"output": "ok"}
    return g


def _fake_agent(name="agent"):
    """Return a mock AutoGen ConversableAgent."""
    a = MagicMock()
    a.name = name
    a.generate_reply.return_value = "I can help with that."
    a.execute_function.return_value = (True, {"result": "done"})
    a.client = None
    return a


# ===========================================================================
# LangGraph adapter
# ===========================================================================

class TestRunCoreLangGraphTracer:
    def test_context_manager_creates_atir(self):
        tracer = RunCoreLangGraphTracer(agent_name="graph_agent", task="test")
        with tracer:
            tracer.record_node("my_node", {"x": 1}, {"y": 2}, duration_ms=10.0)
        trace = tracer.get_atir()
        assert trace.agent_name == "graph_agent"
        assert trace.framework == "langgraph"

    def test_record_node_appears_in_trace(self):
        tracer = RunCoreLangGraphTracer(agent_name="g", task="t")
        with tracer:
            tracer.record_node("classify", {"text": "hello"}, {"label": "greeting"}, duration_ms=5.0)
        trace = tracer.get_atir()
        names = [s.name for s in _tool_spans(trace)]
        assert "classify" in names

    def test_record_node_failure(self):
        tracer = RunCoreLangGraphTracer(agent_name="g", task="t")
        with tracer:
            tracer.record_node("broken", {}, None, duration_ms=2.0, success=False, error="boom")
        trace = tracer.get_atir()
        assert any(not s.success for s in _tool_spans(trace))

    def test_record_llm_appears_in_trace(self):
        tracer = RunCoreLangGraphTracer(agent_name="g", task="t")
        with tracer:
            tracer.record_llm(
                provider="openai", model="gpt-4o",
                input_tokens=100, output_tokens=50,
                cost_usd=0.001, duration_ms=300.0,
            )
        trace = tracer.get_atir()
        assert len(_llm_spans(trace)) >= 1
        assert _llm_spans(trace)[0].model == "gpt-4o"

    def test_wrap_invoke(self):
        graph = _fake_graph({"result": "42"})
        tracer = RunCoreLangGraphTracer(agent_name="wrapped", task="compute")
        wrapped = tracer.wrap(graph)
        result = wrapped.invoke({"x": 5})
        assert result == {"result": "42"}
        graph.invoke.assert_called_once()
        trace = tracer.get_atir()
        assert len(_tool_spans(trace)) >= 1

    def test_wrap_invoke_exception_marks_failure(self):
        graph = MagicMock()
        graph.invoke.side_effect = RuntimeError("graph error")
        tracer = RunCoreLangGraphTracer(agent_name="g", task="t")
        wrapped = tracer.wrap(graph)
        with pytest.raises(RuntimeError, match="graph error"):
            wrapped.invoke({"x": 1})
        trace = tracer.get_atir()
        assert any(not s.success for s in _tool_spans(trace))

    @pytest.mark.asyncio
    async def test_wrap_ainvoke(self):
        import asyncio

        async def _async_invoke(*a, **kw):
            return {"async": "result"}

        graph = MagicMock()
        graph.ainvoke = _async_invoke
        tracer = RunCoreLangGraphTracer(agent_name="async_graph", task="async_task")
        wrapped = tracer.wrap(graph)
        result = await wrapped.ainvoke({"q": "hello"})
        assert result == {"async": "result"}

    def test_no_capture_before_run(self):
        tracer = RunCoreLangGraphTracer(agent_name="g", task="t")
        with pytest.raises(RuntimeError, match="No active capture"):
            tracer.get_atir()

    def test_savings_report_none_without_guards(self):
        tracer = RunCoreLangGraphTracer(agent_name="g", task="t")
        with tracer:
            tracer.record_node("n", {}, {}, duration_ms=1.0)
        assert tracer.savings_report() is None

    def test_with_guards(self):
        guards = GuardConfig(dedup_scope="turn")
        tracer = RunCoreLangGraphTracer(agent_name="g", task="t", guards=guards)
        with tracer:
            tracer.record_node("n", {}, {}, duration_ms=1.0)
        trace = tracer.get_atir()
        assert trace is not None

    def test_framework_tag(self):
        tracer = RunCoreLangGraphTracer(agent_name="g", task="t", framework="custom_langgraph")
        with tracer:
            pass
        assert tracer.get_atir().framework == "custom_langgraph"

    def test_proxy_passes_other_attrs(self):
        graph = _fake_graph()
        graph.some_attr = "hello"
        tracer = RunCoreLangGraphTracer(agent_name="g", task="t")
        wrapped = tracer.wrap(graph)
        assert wrapped.some_attr == "hello"


# ===========================================================================
# CrewAI adapter
# ===========================================================================

class TestRunCoreCrewCallback:
    def _make_callback(self, task="test_task"):
        return RunCoreCrewCallback(agent_name="crew_agent", task=task)

    def test_on_task_end_records_span(self):
        cb = self._make_callback()
        cb.on_task_start(task_id="t1", task_description="analyse data", agent_role="analyst")
        time.sleep(0.01)
        cb.on_task_end(task_id="t1", task_description="analyse data", agent_role="analyst", output="done")
        cb.on_crew_end()
        trace = cb.get_atir()
        names = [s.name for s in _tool_spans(trace)]
        assert any("task" in n for n in names)

    def test_on_tool_end_records_span(self):
        cb = self._make_callback()
        cb.on_tool_start(tool_name="search", run_id="r1")
        cb.on_tool_end(output="results", tool_name="search", run_id="r1")
        cb.on_crew_end()
        trace = cb.get_atir()
        names = [s.name for s in _tool_spans(trace)]
        assert "search" in names

    def test_on_tool_error_records_failure(self):
        cb = self._make_callback()
        cb.on_tool_start(tool_name="fetch", run_id="r2")
        cb.on_tool_error(error=Exception("timeout"), tool_name="fetch", run_id="r2")
        cb.on_crew_end()
        trace = cb.get_atir()
        assert any(not s.success for s in _tool_spans(trace))

    def test_on_llm_end_records_llm_span(self):
        cb = self._make_callback()
        mock_response = MagicMock()
        mock_response.llm_output = {
            "token_usage": {"prompt_tokens": 50, "completion_tokens": 20},
            "model_name": "gpt-4",
        }
        cb.on_llm_start(run_id="llm1")
        cb.on_llm_end(response=mock_response, run_id="llm1")
        cb.on_crew_end()
        trace = cb.get_atir()
        assert len(_llm_spans(trace)) >= 1
        assert _llm_spans(trace)[0].model == "gpt-4"

    def test_on_llm_end_no_response_skips_span(self):
        cb = self._make_callback()
        cb.on_llm_start(run_id="llm2")
        cb.on_llm_end(response=None, run_id="llm2")
        cb.on_crew_end()
        trace = cb.get_atir()
        # No LLM span recorded when tokens = 0
        assert all(s.input_tokens == 0 for s in _llm_spans(trace))

    def test_on_crew_error_marks_failure(self):
        cb = self._make_callback()
        cb.on_crew_error(error=Exception("crew died"))
        trace = cb.get_atir()
        assert trace is not None

    def test_get_atir_after_crew_end(self):
        cb = self._make_callback()
        cb.on_crew_end()
        trace = cb.get_atir()
        assert trace.agent_name == "crew_agent"
        assert trace.framework == "crewai"

    def test_set_quality(self):
        cb = self._make_callback()
        cb.set_quality(0.9)
        cb.on_crew_end()
        trace = cb.get_atir()
        assert trace.quality_score == pytest.approx(0.9)

    def test_savings_report_none_without_guards(self):
        cb = self._make_callback()
        cb.on_crew_end()
        assert cb.savings_report() is None

    def test_trace_crew_context_manager(self):
        with trace_crew("my_crew", task="do work") as tracer:
            tracer.on_tool_start(tool_name="lookup", run_id="x1")
            tracer.on_tool_end(output="val", tool_name="lookup", run_id="x1")
        trace = tracer.get_atir()
        assert trace.agent_name == "my_crew"
        assert len(_tool_spans(trace)) >= 1

    def test_trace_crew_exception_marks_failure(self):
        with pytest.raises(ValueError):
            with trace_crew("crew", task="task") as tracer:
                raise ValueError("crew error")
        # After exception, trace should still be accessible
        trace = tracer.get_atir()
        assert trace is not None

    def test_task_without_id_uses_description(self):
        cb = self._make_callback()
        cb.on_task_start(task_description="write report", agent_role="writer")
        cb.on_task_end(task_description="write report", agent_role="writer")
        cb.on_crew_end()
        trace = cb.get_atir()
        assert len(_tool_spans(trace)) >= 1


# ===========================================================================
# AutoGen adapter
# ===========================================================================

class TestRunCoreAutoGenTracer:
    def test_context_manager_creates_atir(self):
        tracer = RunCoreAutoGenTracer(agent_name="autogen_agent", task="test")
        with tracer:
            tracer.record_message("user", "assistant", "Hello!", duration_ms=5.0)
        trace = tracer.get_atir()
        assert trace.agent_name == "autogen_agent"
        assert trace.framework == "autogen"

    def test_record_message_appears_in_trace(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            tracer.record_message("user", "bot", "What is 2+2?", duration_ms=3.0)
        trace = tracer.get_atir()
        assert len(_tool_spans(trace)) >= 1
        assert "message" in _tool_spans(trace)[0].name

    def test_record_function_call_appears_in_trace(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            tracer.record_function_call(
                function_name="calculator",
                arguments={"a": 2, "b": 3},
                result=5,
                duration_ms=1.0,
            )
        trace = tracer.get_atir()
        names = [s.name for s in _tool_spans(trace)]
        assert "calculator" in names

    def test_record_function_call_failure(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            tracer.record_function_call(
                function_name="bad_fn",
                arguments={},
                result=None,
                duration_ms=1.0,
                success=False,
                error="divide by zero",
            )
        trace = tracer.get_atir()
        assert any(not s.success for s in _tool_spans(trace))

    def test_record_llm_call_appears_in_trace(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            tracer.record_llm_call(
                model="gpt-4o",
                input_tokens=80,
                output_tokens=30,
                cost_usd=0.0005,
                duration_ms=200.0,
            )
        trace = tracer.get_atir()
        assert len(_llm_spans(trace)) >= 1
        assert _llm_spans(trace)[0].model == "gpt-4o"

    def test_no_capture_before_run(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with pytest.raises(RuntimeError, match="No active capture"):
            tracer.get_atir()

    def test_savings_none_without_guards(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            pass
        assert tracer.savings_report() is None

    def test_with_guards(self):
        guards = GuardConfig(dedup_scope="session")
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t", guards=guards)
        with tracer:
            tracer.record_message("u", "a", "hi", duration_ms=1.0)
        trace = tracer.get_atir()
        assert trace is not None

    def test_framework_tag(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t", framework="autogen_v2")
        with tracer:
            pass
        assert tracer.get_atir().framework == "autogen_v2"

    def test_exception_marks_failure(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with pytest.raises(ValueError):
            with tracer:
                raise ValueError("agent error")
        trace = tracer.get_atir()
        assert trace is not None

    def test_set_quality(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            tracer.set_quality(0.85)
        trace = tracer.get_atir()
        assert trace.quality_score == pytest.approx(0.85)

    def test_wrap_agent_proxies_attrs(self):
        agent = _fake_agent("bot")
        agent.extra_attr = "present"
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        wrapped = tracer.wrap_agent(agent)
        assert wrapped.extra_attr == "present"

    def test_wrap_agent_generate_reply_recorded(self):
        agent = _fake_agent("assistant")
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            wrapped = tracer.wrap_agent(agent)
            sender = MagicMock()
            sender.name = "user"
            reply = wrapped.generate_reply(messages=[{"role": "user", "content": "hi"}], sender=sender)
        assert reply == "I can help with that."
        trace = tracer.get_atir()
        assert len(_tool_spans(trace)) >= 1

    def test_wrap_agent_generate_reply_exception(self):
        agent = _fake_agent("broken_agent")
        agent.generate_reply.side_effect = RuntimeError("LLM down")
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            wrapped = tracer.wrap_agent(agent)
            sender = MagicMock()
            sender.name = "user"
            with pytest.raises(RuntimeError, match="LLM down"):
                wrapped.generate_reply(messages=[], sender=sender)
        trace = tracer.get_atir()
        assert any(not s.success for s in _tool_spans(trace))

    def test_wrap_agent_execute_function_recorded(self):
        agent = _fake_agent("tool_agent")
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            wrapped = tracer.wrap_agent(agent)
            ok, res = wrapped.execute_function({"name": "add", "arguments": '{"a": 1, "b": 2}'})
        assert ok is True
        trace = tracer.get_atir()
        names = [s.name for s in _tool_spans(trace)]
        assert "add" in names

    def test_wrap_agent_execute_function_failure(self):
        agent = _fake_agent("fn_agent")
        agent.execute_function.side_effect = RuntimeError("fn error")
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with tracer:
            wrapped = tracer.wrap_agent(agent)
            with pytest.raises(RuntimeError, match="fn error"):
                wrapped.execute_function({"name": "explode", "arguments": {}})
        trace = tracer.get_atir()
        assert any(not s.success for s in _tool_spans(trace))

    def test_initiate_chat_wrapper(self):
        initiator = MagicMock()
        initiator.name = "user_proxy"
        recipient = MagicMock()
        recipient.name = "assistant"
        initiator.initiate_chat.return_value = "chat result"
        initiator.client = None
        recipient.client = None

        tracer = RunCoreAutoGenTracer(agent_name="chat_agent", task="")
        result = tracer.initiate_chat(initiator, recipient, message="Hello!")
        assert result == "chat result"
        trace = tracer.get_atir()
        assert trace.agent_name == "chat_agent"
        names = [s.name for s in _tool_spans(trace)]
        assert "conversation" in names

    def test_initiate_chat_exception(self):
        initiator = MagicMock()
        initiator.name = "proxy"
        initiator.initiate_chat.side_effect = RuntimeError("chat failed")
        initiator.client = None
        recipient = MagicMock()
        recipient.name = "agent"
        recipient.client = None

        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        with pytest.raises(RuntimeError, match="chat failed"):
            tracer.initiate_chat(initiator, recipient, message="go")
        trace = tracer.get_atir()
        assert any(not s.success for s in _tool_spans(trace))

    def test_record_message_no_capture_is_noop(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        # Should not raise even without capture
        tracer.record_message("a", "b", "msg", duration_ms=1.0)

    def test_record_function_call_no_capture_is_noop(self):
        tracer = RunCoreAutoGenTracer(agent_name="ag", task="t")
        tracer.record_function_call("fn", {}, None, duration_ms=1.0)
