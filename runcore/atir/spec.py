"""ATIR v1 — Agent Trace Intermediate Representation.

A provider-agnostic, versioned standard format for AI agent execution traces.
Designed to be imported, exported, and compared across any LLM provider or
agent framework.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


ATIR_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Span types
# ---------------------------------------------------------------------------

class LLMSpan(BaseModel):
    """A single LLM inference call."""
    span_id: str = Field(default_factory=_new_id)
    type: Literal["llm_call"] = "llm_call"
    provider: str = Field(..., description="e.g. 'anthropic', 'openai', 'google'")
    model: str = Field(..., description="Model ID used")
    started_at: datetime = Field(default_factory=_utcnow)
    duration_ms: float = Field(ge=0.0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    stop_reason: str | None = None
    messages_count: int = Field(default=0, ge=0, description="Number of messages in the prompt")
    tools_count: int = Field(default=0, ge=0, description="Number of tools passed")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": False}


class ToolSpan(BaseModel):
    """A single tool / function call."""
    span_id: str = Field(default_factory=_new_id)
    type: Literal["tool_call"] = "tool_call"
    name: str = Field(..., description="Tool name")
    started_at: datetime = Field(default_factory=_utcnow)
    duration_ms: float = Field(ge=0.0)
    input_tokens: int = Field(default=0, ge=0)
    success: bool
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = Field(default="", description="Short text summary of result (not the full result)")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": False}


Span = LLMSpan | ToolSpan


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------

class ATIRAggregates(BaseModel):
    total_tokens: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0.0)
    total_duration_ms: float = Field(ge=0.0)
    llm_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    successful_tool_calls: int = Field(ge=0)
    duplicate_tool_calls: int = Field(default=0, ge=0, description="Tool calls with repeated signature")
    cost_per_successful_task: float = Field(default=0.0, ge=0.0)

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Root trace
# ---------------------------------------------------------------------------

class ATIRTrace(BaseModel):
    """Root ATIR v1 trace document.

    This is the canonical cross-provider format.  Import from any framework
    with ``ATIRTrace.from_agent_trace()``, or use ``runcore.capture()`` to
    produce one directly.
    """
    atir_version: str = Field(default=ATIR_VERSION, description="ATIR spec version")
    trace_id: str = Field(default_factory=_new_id)
    agent_name: str
    task: str
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    success: bool
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    provider: str = Field(default="unknown", description="Primary LLM provider")
    framework: str = Field(default="unknown", description="Agent framework used, e.g. 'langchain', 'runcore'")
    spans: list[LLMSpan | ToolSpan] = Field(default_factory=list)
    aggregates: ATIRAggregates | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    savings: dict[str, Any] | None = Field(default=None, description="Runtime guard savings report")

    model_config = {"frozen": False}

    def compute_aggregates(self) -> ATIRAggregates:
        """Compute aggregates from spans."""
        llm_spans = [s for s in self.spans if s.type == "llm_call"]
        tool_spans = [s for s in self.spans if s.type == "tool_call"]

        input_tok = sum(s.input_tokens for s in llm_spans)
        output_tok = sum(s.output_tokens for s in llm_spans)
        total_cost = sum(s.cost_usd for s in llm_spans)
        duration = sum(s.duration_ms for s in (llm_spans + tool_spans))  # type: ignore[operator]
        success_tools = sum(1 for s in tool_spans if s.success)

        # Detect duplicates: same name + same arg keys pattern
        import json as _json
        seen_sigs: set[str] = set()
        dups = 0
        for s in tool_spans:
            sig = f"{s.name}:{_json.dumps(s.arguments, sort_keys=True)}"
            if sig in seen_sigs:
                dups += 1
            seen_sigs.add(sig)

        cpst = total_cost / max(1, success_tools) if success_tools else total_cost

        return ATIRAggregates(
            total_tokens=input_tok + output_tok,
            input_tokens=input_tok,
            output_tokens=output_tok,
            total_cost_usd=total_cost,
            total_duration_ms=duration,
            llm_calls=len(llm_spans),
            tool_calls=len(tool_spans),
            successful_tool_calls=success_tools,
            duplicate_tool_calls=dups,
            cost_per_successful_task=round(cpst, 6),
        )

    def finalize(self) -> "ATIRTrace":
        """Set finished_at and aggregates."""
        if self.finished_at is None:
            self.finished_at = _utcnow()
        self.aggregates = self.compute_aggregates()
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
