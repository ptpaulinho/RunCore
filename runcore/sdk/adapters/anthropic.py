"""Anthropic-specific helpers — higher-level than the proxy patch."""
from __future__ import annotations

from typing import Any

from runcore.sdk.capture import Capture


def capture_from_response(
    response: Any,
    task: str = "",
    agent_name: str = "anthropic_agent",
    request_messages: list[dict] | None = None,
    request_tools: list[dict] | None = None,
    duration_ms: float = 0.0,
) -> Capture:
    """Create a Capture and populate it from a single Anthropic API response.

    Useful when you already have a response object and want a trace for it::

        response = client.messages.create(...)
        cap = runcore.adapters.anthropic.capture_from_response(
            response, task="classify intent"
        )
        trace = cap.get_atir()
    """
    cap = Capture(agent_name=agent_name, task=task, framework="anthropic")

    usage = getattr(response, "usage", None)
    input_tok = getattr(usage, "input_tokens", 0) if usage else 0
    output_tok = getattr(usage, "output_tokens", 0) if usage else 0
    model = getattr(response, "model", "claude-3-5-sonnet-20241022")

    try:
        from runcore.trace.cost import calculate_llm_cost
        cost = calculate_llm_cost(model, input_tok, output_tok)
    except Exception:
        cost = 0.0

    stop_reason = getattr(response, "stop_reason", None)

    cap.record_llm(
        provider="anthropic",
        model=model,
        input_tokens=input_tok,
        output_tokens=output_tok,
        cost_usd=cost,
        duration_ms=duration_ms,
        stop_reason=str(stop_reason) if stop_reason else None,
        messages_count=len(request_messages or []),
        tools_count=len(request_tools or []),
    )

    # Record any tool calls in the response content
    content = getattr(response, "content", [])
    for block in content:
        if getattr(block, "type", None) == "tool_use":
            cap.record_tool(
                name=getattr(block, "name", "unknown"),
                arguments=getattr(block, "input", {}),
                result=None,
                success=True,
                duration_ms=0.0,
            )

    return cap
