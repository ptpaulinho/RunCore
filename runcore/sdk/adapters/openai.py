"""OpenAI-specific helpers — higher-level than the proxy patch."""
from __future__ import annotations

from typing import Any

from runcore.sdk.capture import Capture


def capture_from_response(
    response: Any,
    task: str = "",
    agent_name: str = "openai_agent",
    request_messages: list[dict] | None = None,
    request_tools: list[dict] | None = None,
    duration_ms: float = 0.0,
) -> Capture:
    """Create a Capture and populate it from a single OpenAI API response.

    Useful when you already have a response object and want a trace for it::

        response = client.chat.completions.create(...)
        cap = runcore.adapters.openai.capture_from_response(
            response, task="classify intent"
        )
        trace = cap.get_atir()
    """
    cap = Capture(agent_name=agent_name, task=task, framework="openai")

    usage = getattr(response, "usage", None)
    input_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
    output_tok = getattr(usage, "completion_tokens", 0) if usage else 0
    model = getattr(response, "model", "gpt-4")

    try:
        from runcore.trace.cost import calculate_llm_cost
        cost = calculate_llm_cost(model, input_tok, output_tok)
    except Exception:
        cost = 0.0

    choices = getattr(response, "choices", [])
    stop_reason = choices[0].finish_reason if choices else None

    cap.record_llm(
        provider="openai",
        model=model,
        input_tokens=input_tok,
        output_tokens=output_tok,
        cost_usd=cost,
        duration_ms=duration_ms,
        stop_reason=str(stop_reason) if stop_reason else None,
        messages_count=len(request_messages or []),
        tools_count=len(request_tools or []),
    )

    # Record any tool calls from choices
    for choice in choices:
        message = getattr(choice, "message", None)
        if message is None:
            continue
        tool_calls = getattr(message, "tool_calls", None) or []
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            import json
            try:
                args = json.loads(getattr(fn, "arguments", "{}"))
            except Exception:
                args = {}
            cap.record_tool(
                name=getattr(fn, "name", "unknown"),
                arguments=args,
                result=None,
                success=True,
                duration_ms=0.0,
            )

    return cap
