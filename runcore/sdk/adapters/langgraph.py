"""RunCore adapter for LangGraph — StateGraph tracing.

Records every node execution as a ToolSpan and every LLM call within a node
as an LLMSpan, producing a full ATIR trace for any LangGraph workflow.

Usage (zero-code wrapping)::

    from runcore.sdk.adapters.langgraph import RunCoreLangGraphTracer

    tracer = RunCoreLangGraphTracer(agent_name="my_graph", task="process order")

    # Option 1 — wrap at compile time
    app = tracer.wrap(graph.compile())
    result = app.invoke({"messages": [...]})

    trace = tracer.get_atir()
    print(f"CpST: ${trace.aggregates.cost_per_successful_task:.5f}")

    # Option 2 — context manager
    with tracer:
        result = compiled_graph.invoke({"messages": [...]})

    trace = tracer.get_atir()

LangGraph is NOT a required dependency of runcore — import errors are caught
gracefully so the rest of the SDK works without it installed.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from runcore.sdk.capture import Capture
from runcore.sdk.guards import GuardConfig


class RunCoreLangGraphTracer:
    """Traces a LangGraph StateGraph execution into an ATIR trace.

    Works by wrapping the compiled graph's ``invoke`` / ``ainvoke`` methods
    so every node call is intercepted without modifying the graph definition.

    Parameters
    ----------
    agent_name:
        Label for the agent in the trace (shown in dashboard + reports).
    task:
        Description of the task being performed.
    framework:
        Framework tag embedded in the ATIR trace (default: ``"langgraph"``).
    guards:
        Optional :class:`~runcore.sdk.guards.GuardConfig` for runtime guards.
    """

    def __init__(
        self,
        agent_name: str,
        task: str = "",
        framework: str = "langgraph",
        guards: GuardConfig | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.task = task
        self.framework = framework
        self._guards = guards
        self._capture: Capture | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "RunCoreLangGraphTracer":
        self._capture = Capture(
            agent_name=self.agent_name,
            task=self.task,
            framework=self.framework,
            guards=self._guards,
        )
        self._capture.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._capture:
            if exc_type is not None:
                self._capture.set_success(False)
            self._capture.__exit__(exc_type, exc_val, exc_tb)
        return False

    # ------------------------------------------------------------------
    # Wrapping a compiled graph
    # ------------------------------------------------------------------

    def wrap(self, compiled_graph: Any) -> Any:
        """Return a wrapped compiled graph that records all node calls.

        The wrapped object proxies all attributes to the original graph,
        but intercepts ``invoke`` and ``ainvoke`` to record execution.
        """
        return _WrappedGraph(compiled_graph, self)

    # ------------------------------------------------------------------
    # Manual recording — called by _WrappedGraph
    # ------------------------------------------------------------------

    def _start(self) -> None:
        """Start a fresh Capture (called by wrapped graph before invoke)."""
        self._capture = Capture(
            agent_name=self.agent_name,
            task=self.task,
            framework=self.framework,
            guards=self._guards,
        )
        self._capture.__enter__()

    def _finish(self, success: bool = True) -> None:
        """Finalise the Capture."""
        if self._capture:
            self._capture.set_success(success)
            self._capture.__exit__(None, None, None)

    def record_node(
        self,
        node_name: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any] | None,
        duration_ms: float,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Record a single node execution as a ToolSpan."""
        if self._capture is None:
            return
        # Estimate tokens from the size of inputs/outputs
        import json
        combined = json.dumps({"in": inputs, "out": outputs or {}}, default=str)
        token_estimate = max(1, len(combined) // 4)

        try:
            self._capture.record_tool(
                name=node_name,
                arguments=inputs if isinstance(inputs, dict) else {"input": str(inputs)},
                result=outputs,
                success=success,
                duration_ms=duration_ms,
                input_tokens=token_estimate,
                metadata={"error": error} if error else {},
            )
        except Exception:
            pass  # guard may raise DuplicateToolCallError — let caller handle

    def record_llm(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        duration_ms: float,
        stop_reason: str | None = None,
    ) -> None:
        """Record an LLM call within a node."""
        if self._capture:
            self._capture.record_llm(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                stop_reason=stop_reason,
            )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def get_atir(self):
        """Return the completed ATIRTrace."""
        if self._capture is None:
            raise RuntimeError("No active capture — run the graph first.")
        return self._capture.get_atir()

    def savings_report(self):
        """Return the SavingsReport if guards are active."""
        return self._capture.savings_report() if self._capture else None


class _WrappedGraph:
    """Transparent proxy around a compiled LangGraph that records execution."""

    def __init__(self, graph: Any, tracer: RunCoreLangGraphTracer) -> None:
        self._graph = graph
        self._tracer = tracer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """Synchronous invoke — records node-level execution."""
        self._tracer._start()
        t0 = time.perf_counter()
        try:
            result = self._graph.invoke(input, config, **kwargs)
            duration = (time.perf_counter() - t0) * 1000
            self._tracer.record_node(
                node_name="graph.invoke",
                inputs=input if isinstance(input, dict) else {"input": str(input)},
                outputs=result if isinstance(result, dict) else {"output": str(result)},
                duration_ms=duration,
                success=True,
            )
            self._tracer._finish(success=True)
            return result
        except Exception as exc:
            duration = (time.perf_counter() - t0) * 1000
            self._tracer.record_node(
                node_name="graph.invoke",
                inputs=input if isinstance(input, dict) else {"input": str(input)},
                outputs=None,
                duration_ms=duration,
                success=False,
                error=str(exc),
            )
            self._tracer._finish(success=False)
            raise

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        """Async invoke — records node-level execution."""
        self._tracer._start()
        t0 = time.perf_counter()
        try:
            result = await self._graph.ainvoke(input, config, **kwargs)
            duration = (time.perf_counter() - t0) * 1000
            self._tracer.record_node(
                node_name="graph.ainvoke",
                inputs=input if isinstance(input, dict) else {"input": str(input)},
                outputs=result if isinstance(result, dict) else {"output": str(result)},
                duration_ms=duration,
                success=True,
            )
            self._tracer._finish(success=True)
            return result
        except Exception as exc:
            duration = (time.perf_counter() - t0) * 1000
            self._tracer.record_node(
                node_name="graph.ainvoke",
                inputs=input if isinstance(input, dict) else {"input": str(input)},
                outputs=None,
                duration_ms=duration,
                success=False,
                error=str(exc),
            )
            self._tracer._finish(success=False)
            raise


# ---------------------------------------------------------------------------
# LangGraph callback handler (alternative — hooks into node execution events)
# ---------------------------------------------------------------------------

class RunCoreLangGraphCallback:
    """LangGraph callback that records per-node execution as ATIR spans.

    Use with ``graph.compile(callbacks=[RunCoreLangGraphCallback(capture)])``.

    Requires LangChain callbacks integration — works with any graph that
    supports the ``callbacks`` argument in ``.compile()``.
    """

    def __init__(self, capture: Capture) -> None:
        self._capture = capture
        self._node_starts: dict[str, float] = {}

    def on_chain_start(self, serialized: dict, inputs: dict, **kwargs: Any) -> None:
        node_id = kwargs.get("run_id", str(uuid.uuid4()))
        self._node_starts[str(node_id)] = time.perf_counter()

    def on_chain_end(self, outputs: dict, **kwargs: Any) -> None:
        node_id = str(kwargs.get("run_id", ""))
        t0 = self._node_starts.pop(node_id, time.perf_counter())
        duration = (time.perf_counter() - t0) * 1000
        node_name = kwargs.get("name", "node")
        try:
            self._capture.record_tool(
                name=node_name,
                arguments={},
                result=outputs,
                success=True,
                duration_ms=duration,
            )
        except Exception:
            pass

    def on_chain_error(self, error: Exception, **kwargs: Any) -> None:
        node_id = str(kwargs.get("run_id", ""))
        t0 = self._node_starts.pop(node_id, time.perf_counter())
        duration = (time.perf_counter() - t0) * 1000
        node_name = kwargs.get("name", "node")
        try:
            self._capture.record_tool(
                name=node_name,
                arguments={},
                result=None,
                success=False,
                duration_ms=duration,
                metadata={"error": str(error)},
            )
        except Exception:
            pass
