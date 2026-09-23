"""TraceCollector — runtime collector for agent traces."""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from runcore.core.models import AgentTrace, LLMCall, ToolCall
from runcore.trace.cost import calculate_llm_cost, calculate_tool_cost
from runcore.trace.storage import save_trace


class TraceCollector:
    """Collect LLM and tool calls during an agent run and produce AgentTrace objects.

    Usage::

        collector = TraceCollector()
        run_id = collector.start_run("my-agent", "summarise document")
        llm = collector.record_llm_call(run_id, "gpt-4", 200, 150, 1200.0)
        tool = collector.record_tool_call(run_id, "search", {"q": "foo"}, ["bar"], True, 300.0)
        collector.end_run(run_id, success=True, quality_score=0.9)
        trace = collector.get_trace(run_id)
    """

    def __init__(self) -> None:
        # Completed and in-progress traces keyed by run_id
        self._traces: dict[str, AgentTrace] = {}
        # Wall-clock start times for latency calculation
        self._start_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(self, agent_name: str, task: str) -> str:
        """Begin a new agent run.

        Args:
            agent_name: Human-readable name of the agent.
            task: Description of the task being performed.

        Returns:
            A unique run_id string that must be passed to subsequent calls.
        """
        run_id = str(uuid.uuid4())
        self._start_times[run_id] = time.monotonic()
        # Create a placeholder trace (success=False until end_run is called)
        trace = AgentTrace(
            run_id=run_id,
            agent_name=agent_name,
            task=task,
            success=False,
        )
        self._traces[run_id] = trace
        return run_id

    def end_run(
        self,
        run_id: str,
        success: bool,
        quality_score: Optional[float] = None,
    ) -> None:
        """Finalise a run, computing wall-clock latency and aggregated totals.

        Args:
            run_id: The run identifier returned by start_run.
            success: Whether the agent completed the task successfully.
            quality_score: Optional quality score in [0, 1].

        Raises:
            KeyError: If run_id is not recognised.
        """
        trace = self._get_trace_or_raise(run_id)
        elapsed_ms = (time.monotonic() - self._start_times.pop(run_id, 0.0)) * 1000.0

        trace.success = success
        trace.quality_score = quality_score
        trace.latency_ms = elapsed_ms

        # Recompute aggregates
        trace.total_cost = sum(c.cost for c in trace.llm_calls) + sum(
            t.cost for t in trace.tool_calls
        )
        trace.total_tokens = sum(c.total_tokens for c in trace.llm_calls) + sum(
            t.tokens_used for t in trace.tool_calls
        )

    # ------------------------------------------------------------------
    # Recording calls
    # ------------------------------------------------------------------

    def record_llm_call(
        self,
        run_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
    ) -> LLMCall:
        """Record an LLM API call within an existing run.

        Args:
            run_id: The run identifier returned by start_run.
            model: Model identifier (e.g. "gpt-4").
            prompt_tokens: Number of prompt/input tokens consumed.
            completion_tokens: Number of completion/output tokens produced.
            latency_ms: Round-trip latency in milliseconds.

        Returns:
            The created LLMCall instance (already appended to the trace).
        """
        trace = self._get_trace_or_raise(run_id)
        cost = calculate_llm_cost(model, prompt_tokens, completion_tokens)
        call = LLMCall(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            latency_ms=latency_ms,
        )
        trace.llm_calls.append(call)
        return call

    def record_tool_call(
        self,
        run_id: str,
        name: str,
        arguments: dict[str, Any],
        result: Any,
        success: bool,
        latency_ms: float,
        tokens_used: int = 0,
    ) -> ToolCall:
        """Record a tool call within an existing run.

        Args:
            run_id: The run identifier returned by start_run.
            name: Tool name.
            arguments: Arguments passed to the tool.
            result: Value returned by the tool.
            success: Whether the tool call succeeded.
            latency_ms: Round-trip latency in milliseconds.
            tokens_used: Optional token count attributed to this tool call.

        Returns:
            The created ToolCall instance (already appended to the trace).
        """
        trace = self._get_trace_or_raise(run_id)
        cost = calculate_tool_cost(name, tokens_used)
        call = ToolCall(
            name=name,
            arguments=arguments,
            result=result,
            success=success,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            cost=cost,
        )
        trace.tool_calls.append(call)
        return call

    # ------------------------------------------------------------------
    # Retrieval and persistence
    # ------------------------------------------------------------------

    def get_trace(self, run_id: str) -> AgentTrace:
        """Return the AgentTrace for a given run_id.

        Raises:
            KeyError: If run_id is not recognised.
        """
        return self._get_trace_or_raise(run_id)

    def calculate_cost(self, run_id: str) -> float:
        """Return the current accumulated cost (USD) for a run.

        Args:
            run_id: The run identifier.

        Returns:
            Total cost in USD computed from all recorded calls so far.
        """
        trace = self._get_trace_or_raise(run_id)
        return sum(c.cost for c in trace.llm_calls) + sum(
            t.cost for t in trace.tool_calls
        )

    def save_trace(self, run_id: str, path: str) -> None:
        """Persist the trace for run_id to a JSON file.

        Args:
            run_id: The run identifier.
            path: Destination file path. Parent directories are created as needed.
        """
        trace = self._get_trace_or_raise(run_id)
        save_trace(trace, path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_trace_or_raise(self, run_id: str) -> AgentTrace:
        try:
            return self._traces[run_id]
        except KeyError:
            raise KeyError(f"Unknown run_id: {run_id!r}") from None
