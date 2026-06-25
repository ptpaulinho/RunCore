"""Capture context manager — records all instrumented calls made within it."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from runcore.atir.spec import ATIRTrace, LLMSpan, ToolSpan
from runcore.sdk import context as _ctx


class Capture:
    """Records every instrumented LLM and tool call made inside a ``with`` block.

    Pass ``guards=GuardConfig()`` to activate runtime optimization guards:
    duplicate tool call blocking, loop breaking, and auto context compression.

    Usage::

        with runcore.capture("my_agent", guards=GuardConfig()) as cap:
            response = client.messages.create(...)
            cap.record_tool("search", {"q": "foo"}, result, True, 12.0)

        trace = cap.get_atir()
        print(cap.savings_report().summary_line())
    """

    def __init__(
        self,
        agent_name: str,
        task: str = "",
        framework: str = "unknown",
        guards=None,          # GuardConfig | None
    ) -> None:
        self.agent_name = agent_name
        self.task = task
        self.framework = framework
        self.trace_id = str(uuid.uuid4())
        self._started_at = datetime.now(timezone.utc)
        self._spans: list[LLMSpan | ToolSpan] = []
        self._success: bool = True
        self._quality_score: float | None = None

        # Runtime guards (optional)
        self._guard_engine = None
        if guards is not None:
            from runcore.sdk.guards import GuardEngine
            self._guard_engine = GuardEngine(guards)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "Capture":
        _ctx.push(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            self._success = False
        _ctx.pop()
        # Auto-push to Cloud if configured
        from runcore.sdk.cloud import is_configured, push_trace
        if is_configured():
            push_trace(self.get_atir())
        return False  # don't suppress exceptions

    # ------------------------------------------------------------------
    # Recording methods (called by SDK proxies or user code)
    # ------------------------------------------------------------------

    def record_llm(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        duration_ms: float,
        stop_reason: str | None = None,
        messages_count: int = 0,
        tools_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._spans.append(LLMSpan(
            provider=provider,
            model=model,
            started_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            stop_reason=stop_reason,
            messages_count=messages_count,
            tools_count=tools_count,
            metadata=metadata or {},
        ))

    def record_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        result: Any,
        success: bool,
        duration_ms: float,
        input_tokens: int = 0,
        metadata: dict[str, Any] | None = None,
        *,
        skip_guard: bool = False,
    ) -> None:
        """Record a tool call span.

        If a guard engine is active and ``skip_guard=False``, raises
        ``DuplicateToolCallError`` before recording when the call is a
        duplicate within the configured scope.
        """
        if not skip_guard and self._guard_engine is not None:
            self._guard_engine.check_tool_call(name, arguments)

        import json
        result_summary = json.dumps(result, default=str)[:200] if result is not None else ""
        self._spans.append(ToolSpan(
            name=name,
            started_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            success=success,
            arguments=arguments,
            result_summary=result_summary,
            metadata=metadata or {},
        ))

    def new_turn(self) -> None:
        """Signal a new LLM turn — resets turn-scoped dedup state."""
        if self._guard_engine is not None:
            self._guard_engine.new_turn()

    def dedup_check(self, name: str, arguments: dict[str, Any]) -> bool:
        """Register a tool call and report whether it is a duplicate — never raises.

        This is the cooperative form of the dedup guard: instead of aborting the run
        with ``DuplicateToolCallError``, callers use the boolean to serve the call from
        their own result cache (the real saving) and keep going. Updates guard savings
        accounting. Returns False when no guard engine is active.
        """
        if self._guard_engine is None:
            return False
        from runcore.sdk.guards import DuplicateToolCallError
        try:
            self._guard_engine.check_tool_call(name, arguments)
            return False
        except DuplicateToolCallError:
            return True

    def check_loop_risk(self, loop_risk_score: float) -> None:
        """Check loop risk against guard threshold.  Raises LoopBreakError if exceeded."""
        if self._guard_engine is not None:
            self._guard_engine.check_loop_risk(loop_risk_score)

    def compress_messages(self, messages: list[dict], estimated_tokens: int) -> list[dict]:
        """Return (possibly compressed) messages via the context compression guard."""
        if self._guard_engine is not None:
            return self._guard_engine.maybe_compress(messages, estimated_tokens, self.task)
        return messages

    def savings_report(self):
        """Return the SavingsReport accumulated by active guards.

        Returns None if no guard engine is configured.
        """
        if self._guard_engine is None:
            return None
        return self._guard_engine.savings

    def set_success(self, success: bool) -> None:
        self._success = success

    def set_quality(self, score: float) -> None:
        self._quality_score = score

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def get_atir(self) -> ATIRTrace:
        """Return the completed ATIRTrace, including guard savings if active."""
        provider = "unknown"
        for s in self._spans:
            if s.type == "llm_call" and s.provider != "unknown":
                provider = s.provider
                break

        savings_dict = None
        if self._guard_engine is not None:
            savings_dict = self._guard_engine.savings.to_dict()

        atir = ATIRTrace(
            trace_id=self.trace_id,
            agent_name=self.agent_name,
            task=self.task,
            started_at=self._started_at,
            success=self._success,
            quality_score=self._quality_score,
            provider=provider,
            framework=self.framework,
            spans=list(self._spans),
            savings=savings_dict,
        )
        return atir.finalize()

    def get_trace(self):
        """Return the trace as a RunCore ``AgentTrace``."""
        from runcore.atir.converter import atir_to_agent_trace
        return atir_to_agent_trace(self.get_atir())

    def summary(self) -> dict[str, Any]:
        """Quick summary dict — useful for printing."""
        atir = self.get_atir()
        agg = atir.aggregates
        result = {
            "agent": self.agent_name,
            "success": self._success,
            "llm_calls": agg.llm_calls if agg else 0,
            "tool_calls": agg.tool_calls if agg else 0,
            "total_tokens": agg.total_tokens if agg else 0,
            "total_cost_usd": round(agg.total_cost_usd, 6) if agg else 0,
            "total_duration_ms": round(agg.total_duration_ms, 1) if agg else 0,
            "duplicate_tool_calls": agg.duplicate_tool_calls if agg else 0,
        }
        if self._guard_engine is not None:
            result["savings"] = self._guard_engine.savings.to_dict()
        return result
