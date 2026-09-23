"""Tests for ATIR v1 spec, converter, and round-trip correctness."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from runcore.atir.spec import (
    ATIRTrace, ATIRAggregates, LLMSpan, ToolSpan, ATIR_VERSION,
)
from runcore.atir.converter import (
    agent_trace_to_atir,
    atir_to_agent_trace,
    from_openai_response,
    from_anthropic_response,
    from_dict,
)
from runcore.core.models import AgentTrace, LLMCall, ToolCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_span(**kwargs) -> LLMSpan:
    defaults = dict(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        started_at=datetime.now(timezone.utc),
        duration_ms=300.0,
        input_tokens=400,
        output_tokens=80,
        cost_usd=0.00015,
    )
    defaults.update(kwargs)
    return LLMSpan(**defaults)


def _make_tool_span(**kwargs) -> ToolSpan:
    defaults = dict(
        name="get_invoice",
        started_at=datetime.now(timezone.utc),
        duration_ms=12.0,
        input_tokens=30,
        success=True,
        arguments={"invoice_id": "INV-1"},
        result_summary='{"amount": 99.99}',
    )
    defaults.update(kwargs)
    return ToolSpan(**defaults)


def _make_atir_trace(**kwargs) -> ATIRTrace:
    defaults = dict(
        trace_id=str(uuid.uuid4()),
        agent_name="test_agent",
        task="process order",
        started_at=datetime.now(timezone.utc),
        success=True,
        provider="anthropic",
        framework="runcore",
        spans=[_make_llm_span(), _make_tool_span()],
    )
    defaults.update(kwargs)
    return ATIRTrace(**defaults).finalize()


def _make_agent_trace() -> AgentTrace:
    from runcore.core.models import AgentTrace
    return AgentTrace(
        agent_name="test_agent",
        task="process order",
        llm_calls=[LLMCall(
            prompt_tokens=400,
            completion_tokens=80,
            total_tokens=480,
            cost=0.00015,
            latency_ms=300.0,
            model="claude-haiku-4-5-20251001",
        )],
        tool_calls=[ToolCall(
            name="get_invoice",
            args={"invoice_id": "INV-1"},
            result={"amount": 99.99},
            success=True,
            latency_ms=12.0,
        )],
        success=True,
    )


# ---------------------------------------------------------------------------
# Spec: span_id auto-generation
# ---------------------------------------------------------------------------

def test_llm_span_has_span_id():
    span = _make_llm_span()
    assert span.span_id
    assert len(span.span_id) > 10


def test_tool_span_has_span_id():
    span = _make_tool_span()
    assert span.span_id
    assert len(span.span_id) > 10


def test_llm_span_type_literal():
    span = _make_llm_span()
    assert span.type == "llm_call"


def test_tool_span_type_literal():
    span = _make_tool_span()
    assert span.type == "tool_call"


# ---------------------------------------------------------------------------
# Spec: ATIRTrace finalize() and aggregates
# ---------------------------------------------------------------------------

def test_finalize_sets_finished_at():
    trace = _make_atir_trace()
    assert trace.finished_at is not None


def test_finalize_computes_aggregates():
    trace = _make_atir_trace()
    agg = trace.aggregates
    assert agg is not None
    assert agg.llm_calls == 1
    assert agg.tool_calls == 1
    assert agg.successful_tool_calls == 1
    assert agg.input_tokens == 400
    assert agg.output_tokens == 80
    assert agg.total_tokens == 480
    assert abs(agg.total_cost_usd - 0.00015) < 1e-9


def test_aggregates_duplicate_tool_calls():
    span1 = _make_tool_span(arguments={"invoice_id": "INV-1"})
    span2 = _make_tool_span(arguments={"invoice_id": "INV-1"})  # same sig
    trace = ATIRTrace(
        trace_id=str(uuid.uuid4()),
        agent_name="test",
        task="test",
        started_at=datetime.now(timezone.utc),
        success=True,
        provider="anthropic",
        framework="runcore",
        spans=[span1, span2],
    ).finalize()
    assert trace.aggregates.duplicate_tool_calls == 1


def test_cost_per_successful_task():
    trace = _make_atir_trace()
    agg = trace.aggregates
    # CpST = total_cost_usd / max(1, successful_tool_calls)
    expected = agg.total_cost_usd / max(1, agg.successful_tool_calls)
    assert abs(agg.cost_per_successful_task - expected) < 1e-12


# ---------------------------------------------------------------------------
# Spec: to_dict() round-trip
# ---------------------------------------------------------------------------

def test_to_dict_round_trip():
    trace = _make_atir_trace()
    d = trace.to_dict()
    assert d["atir_version"] == ATIR_VERSION
    assert d["agent_name"] == "test_agent"
    assert len(d["spans"]) == 2
    # types survive round-trip
    types = {s["type"] for s in d["spans"]}
    assert types == {"llm_call", "tool_call"}


def test_from_dict_restores_trace():
    trace = _make_atir_trace()
    d = trace.to_dict()
    restored = from_dict(d)
    assert restored.trace_id == trace.trace_id
    assert restored.agent_name == trace.agent_name
    assert len(restored.spans) == 2


def test_from_dict_polymorphic_spans():
    trace = _make_atir_trace()
    d = trace.to_dict()
    restored = from_dict(d)
    llm = [s for s in restored.spans if s.type == "llm_call"]
    tool = [s for s in restored.spans if s.type == "tool_call"]
    assert len(llm) == 1
    assert len(tool) == 1
    assert isinstance(llm[0], LLMSpan)
    assert isinstance(tool[0], ToolSpan)


# ---------------------------------------------------------------------------
# Converter: agent_trace_to_atir / atir_to_agent_trace
# ---------------------------------------------------------------------------

def test_agent_trace_to_atir():
    agent_trace = _make_agent_trace()
    atir = agent_trace_to_atir(agent_trace)
    assert atir.trace_id == agent_trace.run_id
    assert atir.agent_name == agent_trace.agent_name
    assert atir.aggregates.llm_calls == 1
    assert atir.aggregates.tool_calls == 1


def test_atir_to_agent_trace():
    atir = _make_atir_trace()
    agent_trace = atir_to_agent_trace(atir)
    assert agent_trace.run_id == atir.trace_id
    assert agent_trace.agent_name == atir.agent_name
    assert len(agent_trace.llm_calls) == 1
    assert len(agent_trace.tool_calls) == 1


def test_round_trip_agent_trace_to_atir_and_back():
    original = _make_agent_trace()
    atir = agent_trace_to_atir(original)
    recovered = atir_to_agent_trace(atir)
    assert recovered.run_id == original.run_id
    assert len(recovered.llm_calls) == len(original.llm_calls)
    assert len(recovered.tool_calls) == len(original.tool_calls)


# ---------------------------------------------------------------------------
# Converter: from_openai_response / from_anthropic_response
# ---------------------------------------------------------------------------

class _FakeUsage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _FakeChoice:
    def __init__(self):
        self.finish_reason = "stop"
        self.message = type("M", (), {"tool_calls": None})()


class _FakeOpenAIResponse:
    def __init__(self):
        self.model = "gpt-4o"
        self.usage = _FakeUsage(300, 60)
        self.choices = [_FakeChoice()]


def test_from_openai_response():
    resp = _FakeOpenAIResponse()
    atir = from_openai_response(resp, task="classify intent", agent_name="oai_agent")
    assert atir.provider == "openai"
    assert atir.agent_name == "oai_agent"
    agg = atir.aggregates
    assert agg.llm_calls == 1
    assert agg.input_tokens == 300
    assert agg.output_tokens == 60


class _FakeAnthropicUsage:
    def __init__(self, inp, out):
        self.input_tokens = inp
        self.output_tokens = out


class _FakeAnthropicResponse:
    def __init__(self):
        self.model = "claude-3-5-sonnet-20241022"
        self.usage = _FakeAnthropicUsage(500, 100)
        self.stop_reason = "end_turn"
        self.content = []


def test_from_anthropic_response():
    resp = _FakeAnthropicResponse()
    atir = from_anthropic_response(resp, task="summarize", agent_name="claude_agent")
    assert atir.provider == "anthropic"
    assert atir.agent_name == "claude_agent"
    agg = atir.aggregates
    assert agg.llm_calls == 1
    assert agg.input_tokens == 500
    assert agg.output_tokens == 100


# ---------------------------------------------------------------------------
# ATIR version constant
# ---------------------------------------------------------------------------

def test_atir_version():
    assert ATIR_VERSION == "1.0"
