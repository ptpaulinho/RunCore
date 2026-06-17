from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class LLMCall(BaseModel):
    id: str = Field(default_factory=_new_id, description="Unique identifier for this LLM call")
    model: str = Field(..., description="Model identifier, e.g. claude-3-5-sonnet-20241022")
    prompt_tokens: int = Field(..., ge=0, description="Number of tokens in the prompt")
    completion_tokens: int = Field(..., ge=0, description="Number of tokens in the completion")
    cost: float = Field(..., ge=0.0, description="Cost of this call in USD")
    latency_ms: float = Field(..., ge=0.0, description="End-to-end latency in milliseconds")
    timestamp: datetime = Field(default_factory=_utcnow, description="UTC timestamp when the call was made")

    @field_validator("model")
    @classmethod
    def model_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model must not be empty")
        return v

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    model_config = {"frozen": False}


class ToolCall(BaseModel):
    id: str = Field(default_factory=_new_id, description="Unique identifier for this tool call")
    name: str = Field(..., description="Name of the tool that was called")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments passed to the tool")
    result: Any = Field(default=None, description="Return value from the tool")
    success: bool = Field(..., description="Whether the tool call completed successfully")
    latency_ms: float = Field(..., ge=0.0, description="End-to-end latency in milliseconds")
    tokens_used: int = Field(default=0, ge=0, description="Tokens consumed by this tool interaction")
    cost: float = Field(default=0.0, ge=0.0, description="Cost attributed to this tool call in USD")
    timestamp: datetime = Field(default_factory=_utcnow, description="UTC timestamp when the call was made")

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("tool name must not be empty")
        return v

    model_config = {"frozen": False}


class AgentTrace(BaseModel):
    run_id: str = Field(default_factory=_new_id, description="Unique identifier for this agent run")
    agent_name: str = Field(..., description="Name or identifier of the agent")
    task: str = Field(..., description="Description of the task the agent was asked to perform")
    llm_calls: list[LLMCall] = Field(default_factory=list, description="Ordered list of LLM calls made")
    tool_calls: list[ToolCall] = Field(default_factory=list, description="Ordered list of tool calls made")
    total_cost: float = Field(default=0.0, ge=0.0, description="Aggregated cost in USD")
    total_tokens: int = Field(default=0, ge=0, description="Aggregated token count")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Wall-clock latency for the full run in ms")
    success: bool = Field(..., description="Whether the agent completed the task successfully")
    quality_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Optional quality score 0–1")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary key/value metadata")

    @field_validator("agent_name", "task")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field must not be empty")
        return v

    @model_validator(mode="after")
    def sync_aggregates(self) -> AgentTrace:
        """Recompute totals from child records when they are provided but totals are zero."""
        if self.llm_calls and self.total_cost == 0.0:
            self.total_cost = sum(c.cost for c in self.llm_calls) + sum(t.cost for t in self.tool_calls)
        if self.llm_calls and self.total_tokens == 0:
            self.total_tokens = sum(c.total_tokens for c in self.llm_calls) + sum(
                t.tokens_used for t in self.tool_calls
            )
        return self

    model_config = {"frozen": False}


class OptimizationConfig(BaseModel):
    max_tools: int = Field(default=10, ge=1, description="Maximum number of tool calls allowed per run")
    min_quality_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Minimum acceptable quality score after optimization"
    )
    cost_reduction_target: float = Field(
        default=0.3, ge=0.0, le=1.0, description="Desired fractional cost reduction (0.3 = 30%)"
    )
    enable_context_compression: bool = Field(default=True, description="Enable context window compression")
    enable_loop_detection: bool = Field(default=True, description="Detect and handle repeated tool call loops")
    enable_tool_ranking: bool = Field(default=True, description="Rank tools by efficiency and prefer top-ranked ones")

    model_config = {"frozen": False}


class BenchmarkResult(BaseModel):
    baseline: AgentTrace = Field(..., description="The unoptimized agent trace used as baseline")
    optimized: AgentTrace = Field(..., description="The optimized agent trace")
    runs: int = Field(..., ge=1, description="Number of evaluation runs performed")
    cost_savings_pct: float = Field(..., description="Percentage cost reduction (positive = cheaper)")
    token_reduction_pct: float = Field(..., description="Percentage token reduction (positive = fewer tokens)")
    tool_call_reduction_pct: float = Field(
        ..., description="Percentage reduction in tool calls (positive = fewer calls)"
    )
    latency_change_pct: float = Field(..., description="Percentage latency change (negative = faster)")
    success_rate_delta: float = Field(
        ..., ge=-1.0, le=1.0, description="Change in success rate (optimized - baseline)"
    )
    quality_delta: float = Field(
        ..., ge=-1.0, le=1.0, description="Change in quality score (optimized - baseline)"
    )
    result: str = Field(..., description="Overall benchmark verdict, e.g. PASS or FAIL")
    report_path: Optional[str] = Field(default=None, description="Filesystem path to the generated HTML/JSON report")
    replacement_findings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tool calls that could be replaced by deterministic Python code",
    )

    @field_validator("result")
    @classmethod
    def result_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("result must not be empty")
        return v

    model_config = {"frozen": False}
