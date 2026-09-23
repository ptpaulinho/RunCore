"""Bidirectional converter between RunCore's AgentTrace and ATIR v1."""
from __future__ import annotations

import json
from typing import Any

from runcore.atir.spec import ATIRTrace, LLMSpan, ToolSpan


def agent_trace_to_atir(trace, framework: str = "runcore") -> ATIRTrace:
    """Convert a RunCore ``AgentTrace`` to an ``ATIRTrace``."""
    from runcore.core.models import AgentTrace

    spans: list[LLMSpan | ToolSpan] = []

    # Infer provider from the first LLM call's model name
    provider = "unknown"
    if trace.llm_calls:
        m = trace.llm_calls[0].model.lower()
        if "claude" in m:
            provider = "anthropic"
        elif "gpt" in m or "o1" in m or "o3" in m:
            provider = "openai"
        elif "gemini" in m or "palm" in m:
            provider = "google"
        elif "llama" in m or "mistral" in m:
            provider = "meta" if "llama" in m else "mistral"

    for lc in trace.llm_calls:
        spans.append(LLMSpan(
            span_id=lc.id,
            provider=provider,
            model=lc.model,
            started_at=lc.timestamp,
            duration_ms=lc.latency_ms,
            input_tokens=lc.prompt_tokens,
            output_tokens=lc.completion_tokens,
            cost_usd=lc.cost,
        ))

    for tc in trace.tool_calls:
        result_str = json.dumps(tc.result, default=str) if tc.result is not None else ""
        spans.append(ToolSpan(
            span_id=tc.id,
            name=tc.name,
            started_at=tc.timestamp,
            duration_ms=tc.latency_ms,
            input_tokens=tc.tokens_used,
            success=tc.success,
            arguments=tc.arguments,
            result_summary=result_str[:200],
        ))

    # Sort by timestamp so spans are in chronological order
    spans.sort(key=lambda s: s.started_at)

    atir = ATIRTrace(
        trace_id=trace.run_id,
        agent_name=trace.agent_name,
        task=trace.task,
        started_at=trace.llm_calls[0].timestamp if trace.llm_calls else spans[0].started_at if spans else __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        success=trace.success,
        quality_score=trace.quality_score,
        provider=provider,
        framework=framework,
        spans=spans,
        metadata=trace.metadata or {},
    )
    return atir.finalize()


def atir_to_agent_trace(atir: ATIRTrace):
    """Convert an ``ATIRTrace`` to a RunCore ``AgentTrace``.

    Note: some ATIR fields have no direct AgentTrace equivalent and are
    stored in ``metadata``.
    """
    from runcore.core.models import AgentTrace, LLMCall, ToolCall
    from runcore.trace.cost import calculate_llm_cost

    llm_calls: list[LLMCall] = []
    tool_calls: list[ToolCall] = []

    for span in atir.spans:
        if span.type == "llm_call":
            model = span.model
            cost = span.cost_usd or calculate_llm_cost(model, span.input_tokens, span.output_tokens)
            llm_calls.append(LLMCall(
                id=span.span_id,
                model=model,
                prompt_tokens=span.input_tokens,
                completion_tokens=span.output_tokens,
                cost=cost,
                latency_ms=span.duration_ms,
                timestamp=span.started_at,
            ))
        elif span.type == "tool_call":
            tool_calls.append(ToolCall(
                id=span.span_id,
                name=span.name,
                arguments=span.arguments,
                result=span.result_summary,
                success=span.success,
                latency_ms=span.duration_ms,
                tokens_used=span.input_tokens,
                timestamp=span.started_at,
            ))

    agg = atir.aggregates or atir.compute_aggregates()
    return AgentTrace(
        run_id=atir.trace_id,
        agent_name=atir.agent_name,
        task=atir.task,
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        total_cost=agg.total_cost_usd,
        total_tokens=agg.total_tokens,
        latency_ms=agg.total_duration_ms,
        success=atir.success,
        quality_score=atir.quality_score,
        metadata={
            "atir_version": atir.atir_version,
            "framework": atir.framework,
            "provider": atir.provider,
            "tags": atir.tags,
            **atir.metadata,
        },
    )


# ---------------------------------------------------------------------------
# External format importers
# ---------------------------------------------------------------------------

def from_openai_response(response_obj: Any, task: str = "", agent_name: str = "openai_agent") -> ATIRTrace:
    """Build an ATIRTrace from a raw OpenAI ``ChatCompletion`` response."""
    usage = getattr(response_obj, "usage", None) or {}
    if hasattr(usage, "prompt_tokens"):
        input_tok = usage.prompt_tokens or 0
        output_tok = usage.completion_tokens or 0
    else:
        input_tok = usage.get("prompt_tokens", 0)
        output_tok = usage.get("completion_tokens", 0)

    model = getattr(response_obj, "model", "gpt-4")
    from runcore.trace.cost import calculate_llm_cost
    cost = calculate_llm_cost(model, input_tok, output_tok)

    span = LLMSpan(
        provider="openai",
        model=model,
        duration_ms=0.0,
        input_tokens=input_tok,
        output_tokens=output_tok,
        cost_usd=cost,
        stop_reason=str(getattr(response_obj.choices[0], "finish_reason", "")) if hasattr(response_obj, "choices") and response_obj.choices else None,
    )
    atir = ATIRTrace(
        agent_name=agent_name,
        task=task,
        success=True,
        provider="openai",
        framework="openai",
        spans=[span],
    )
    return atir.finalize()


def from_anthropic_response(response_obj: Any, task: str = "", agent_name: str = "anthropic_agent") -> ATIRTrace:
    """Build an ATIRTrace from a raw Anthropic ``Message`` response."""
    usage = getattr(response_obj, "usage", None)
    input_tok = getattr(usage, "input_tokens", 0) if usage else 0
    output_tok = getattr(usage, "output_tokens", 0) if usage else 0

    model = getattr(response_obj, "model", "claude-3-5-sonnet-20241022")
    from runcore.trace.cost import calculate_llm_cost
    cost = calculate_llm_cost(model, input_tok, output_tok)

    span = LLMSpan(
        provider="anthropic",
        model=model,
        duration_ms=0.0,
        input_tokens=input_tok,
        output_tokens=output_tok,
        cost_usd=cost,
        stop_reason=getattr(response_obj, "stop_reason", None),
    )
    atir = ATIRTrace(
        agent_name=agent_name,
        task=task,
        success=True,
        provider="anthropic",
        framework="anthropic",
        spans=[span],
    )
    return atir.finalize()


def from_dict(data: dict[str, Any]) -> ATIRTrace:
    """Load an ATIRTrace from a dict (e.g. parsed JSON)."""
    # Polymorphic span deserialization
    raw_spans = data.pop("spans", [])
    atir = ATIRTrace(**{k: v for k, v in data.items() if k != "spans"})
    for s in raw_spans:
        if s.get("type") == "llm_call":
            atir.spans.append(LLMSpan(**s))
        elif s.get("type") == "tool_call":
            atir.spans.append(ToolSpan(**s))
    return atir
