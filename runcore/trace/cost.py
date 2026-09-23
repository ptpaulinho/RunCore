"""Cost calculation utilities for RunCore trace module."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runcore.trace.tokens import MODEL_COSTS

# Tool costs by tool name (USD per call or per token). Empty means free.
_TOOL_COSTS: dict[str, float] = {}


def calculate_llm_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate cost in USD for an LLM call.

    Args:
        model: Model identifier string.
        prompt_tokens: Number of input/prompt tokens.
        completion_tokens: Number of output/completion tokens.

    Returns:
        Cost in USD as a float. Returns 0.0 if model is not in MODEL_COSTS.
    """
    costs = MODEL_COSTS.get(model)
    if costs is None:
        return 0.0
    return (prompt_tokens * costs["input"]) + (completion_tokens * costs["output"])


def calculate_tool_cost(tool_name: str, tokens_used: int = 0) -> float:
    """Calculate cost for a tool call. Tools are free unless explicitly priced.

    Args:
        tool_name: Name of the tool.
        tokens_used: Tokens consumed by the tool interaction (may affect cost for some tools).

    Returns:
        Cost in USD. 0.0 for tools not listed in _TOOL_COSTS.
    """
    per_token_cost = _TOOL_COSTS.get(tool_name, 0.0)
    return tokens_used * per_token_cost


@dataclass
class CallRecord:
    """Summary of a single LLM or tool call for cost breakdown."""
    call_type: str  # "llm" or "tool"
    name: str       # model name or tool name
    cost: float
    tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CostBreakdown:
    """Detailed cost breakdown for an agent run."""
    llm_cost: float
    tool_cost: float
    total_cost: float
    per_call: list[CallRecord] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "CostBreakdown":
        return cls(llm_cost=0.0, tool_cost=0.0, total_cost=0.0, per_call=[])


def calculate_total_cost(
    llm_calls: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> float:
    """Calculate total cost from lists of LLM and tool call dicts.

    Each LLM call dict should have: model, prompt_tokens, completion_tokens.
    Each tool call dict should have: name, tokens_used (optional).

    Returns:
        Total cost in USD.
    """
    total = 0.0
    for call in llm_calls:
        total += calculate_llm_cost(
            call.get("model", ""),
            call.get("prompt_tokens", 0),
            call.get("completion_tokens", 0),
        )
    for call in tool_calls:
        total += calculate_tool_cost(
            call.get("name", ""),
            call.get("tokens_used", 0),
        )
    return total


def build_cost_breakdown(
    llm_calls: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> CostBreakdown:
    """Build a detailed CostBreakdown from lists of call dicts."""
    per_call: list[CallRecord] = []
    llm_total = 0.0
    tool_total = 0.0

    for call in llm_calls:
        cost = calculate_llm_cost(
            call.get("model", ""),
            call.get("prompt_tokens", 0),
            call.get("completion_tokens", 0),
        )
        llm_total += cost
        per_call.append(
            CallRecord(
                call_type="llm",
                name=call.get("model", "unknown"),
                cost=cost,
                tokens=call.get("prompt_tokens", 0) + call.get("completion_tokens", 0),
                extra={
                    "prompt_tokens": call.get("prompt_tokens", 0),
                    "completion_tokens": call.get("completion_tokens", 0),
                },
            )
        )

    for call in tool_calls:
        tokens_used = call.get("tokens_used", 0)
        cost = calculate_tool_cost(call.get("name", ""), tokens_used)
        tool_total += cost
        per_call.append(
            CallRecord(
                call_type="tool",
                name=call.get("name", "unknown"),
                cost=cost,
                tokens=tokens_used,
            )
        )

    return CostBreakdown(
        llm_cost=llm_total,
        tool_cost=tool_total,
        total_cost=llm_total + tool_total,
        per_call=per_call,
    )
