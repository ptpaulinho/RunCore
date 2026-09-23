"""RunCore adapter for CrewAI.

Records every task execution, tool call, and LLM response in a CrewAI workflow
as ATIR spans, producing a full trace for analysis and optimization.

Usage::

    from crewai import Crew, Agent, Task
    from runcore.sdk.adapters.crewai import RunCoreCrewCallback, trace_crew

    # Option 1 — callback (hook into existing crew)
    callback = RunCoreCrewCallback(agent_name="support_crew", task="handle tickets")
    crew = Crew(agents=[...], tasks=[...], callbacks=[callback])
    result = crew.kickoff()

    trace = callback.get_atir()
    print(f"CpST: ${trace.aggregates.cost_per_successful_task:.5f}")

    # Option 2 — wrap the entire crew.kickoff()
    with trace_crew("support_crew", task="handle tickets") as tracer:
        result = crew.kickoff()

    trace = tracer.get_atir()

CrewAI is NOT a required dependency of runcore.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from runcore.sdk.capture import Capture
from runcore.sdk.guards import GuardConfig


class RunCoreCrewCallback:
    """CrewAI callback that records task and tool execution as ATIR spans.

    Pass an instance to ``Crew(callbacks=[callback])`` or attach it to
    individual agents via ``Agent(callbacks=[callback])``.

    Parameters
    ----------
    agent_name:
        Label for the crew in reports and the dashboard.
    task:
        Description of the overall objective (e.g. ``"process support queue"``).
    framework:
        Framework tag in the ATIR trace (default: ``"crewai"``).
    guards:
        Optional :class:`~runcore.sdk.guards.GuardConfig` for runtime guards.
    """

    def __init__(
        self,
        agent_name: str = "crewai_crew",
        task: str = "",
        framework: str = "crewai",
        guards: GuardConfig | None = None,
    ) -> None:
        self.agent_name = agent_name
        self._capture = Capture(
            agent_name=agent_name,
            task=task,
            framework=framework,
            guards=guards,
        )
        self._capture.__enter__()

        # Timing state keyed by run_id / task_id
        self._task_starts: dict[str, float] = {}
        self._tool_starts: dict[str, float] = {}
        self._llm_starts:  dict[str, float] = {}

    # ------------------------------------------------------------------
    # CrewAI task lifecycle hooks
    # ------------------------------------------------------------------

    def on_task_start(
        self,
        task_output: Any = None,
        *,
        task_id: str | None = None,
        task_description: str = "",
        agent_role: str = "",
        **kwargs: Any,
    ) -> None:
        key = task_id or task_description or str(uuid.uuid4())
        self._task_starts[key] = time.perf_counter()

    def on_task_end(
        self,
        task_output: Any = None,
        *,
        task_id: str | None = None,
        task_description: str = "",
        agent_role: str = "",
        output: Any = None,
        **kwargs: Any,
    ) -> None:
        key = task_id or task_description
        t0 = self._task_starts.pop(key, time.perf_counter())
        duration = (time.perf_counter() - t0) * 1000
        result = output or task_output

        import json
        result_str = json.dumps(result, default=str)[:200] if result else ""
        token_estimate = max(1, len(str(task_description)) // 4)

        try:
            self._capture.record_tool(
                name=f"task:{agent_role}" if agent_role else "task",
                arguments={"description": task_description[:200]},
                result=result_str or None,
                success=True,
                duration_ms=duration,
                input_tokens=token_estimate,
                metadata={"agent_role": agent_role},
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # CrewAI tool lifecycle hooks
    # ------------------------------------------------------------------

    def on_tool_start(
        self,
        tool_name: str = "",
        tool_input: Any = None,
        *,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        key = run_id or tool_name or str(uuid.uuid4())
        self._tool_starts[key] = time.perf_counter()

    def on_tool_end(
        self,
        output: Any = None,
        tool_name: str = "",
        *,
        run_id: str | None = None,
        tool_input: Any = None,
        **kwargs: Any,
    ) -> None:
        key = run_id or tool_name
        t0 = self._tool_starts.pop(key, time.perf_counter())
        duration = (time.perf_counter() - t0) * 1000

        args = tool_input if isinstance(tool_input, dict) else {"input": str(tool_input or "")}
        try:
            self._capture.record_tool(
                name=tool_name or "tool",
                arguments=args,
                result=output,
                success=True,
                duration_ms=duration,
                input_tokens=max(1, len(str(tool_input or "")) // 4),
            )
        except Exception:
            pass

    def on_tool_error(
        self,
        error: Exception | None = None,
        tool_name: str = "",
        *,
        run_id: str | None = None,
        tool_input: Any = None,
        **kwargs: Any,
    ) -> None:
        key = run_id or tool_name
        t0 = self._tool_starts.pop(key, time.perf_counter())
        duration = (time.perf_counter() - t0) * 1000

        args = tool_input if isinstance(tool_input, dict) else {"input": str(tool_input or "")}
        try:
            self._capture.record_tool(
                name=tool_name or "tool",
                arguments=args,
                result=None,
                success=False,
                duration_ms=duration,
                metadata={"error": str(error)},
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # LLM call hooks (CrewAI fires these via its internal LLM wrapper)
    # ------------------------------------------------------------------

    def on_llm_start(
        self,
        serialized: dict | None = None,
        prompts: list | None = None,
        *,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id or uuid.uuid4())
        self._llm_starts[key] = time.perf_counter()

    def on_llm_end(
        self,
        response: Any = None,
        *,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id or "")
        t0 = self._llm_starts.pop(key, time.perf_counter())
        duration = (time.perf_counter() - t0) * 1000

        # Extract token usage from CrewAI/LangChain response format
        input_tokens = 0
        output_tokens = 0
        model = "unknown"

        if response is not None:
            try:
                # LangChain LLMResult format
                if hasattr(response, "llm_output") and response.llm_output:
                    usage = response.llm_output.get("token_usage", {})
                    input_tokens  = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)
                    model = response.llm_output.get("model_name", "unknown")
            except Exception:
                pass

        cost_usd = (input_tokens + output_tokens) * 3e-6
        if input_tokens > 0 or output_tokens > 0:
            self._capture.record_llm(
                provider="crewai",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=duration,
            )

    def on_llm_error(
        self,
        error: Exception | None = None,
        *,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id or "")
        self._llm_starts.pop(key, None)
        self._capture.set_success(False)

    # ------------------------------------------------------------------
    # Crew-level hooks
    # ------------------------------------------------------------------

    def on_crew_start(self, crew_name: str = "", inputs: dict | None = None, **kwargs: Any) -> None:
        pass  # Capture already started in __init__

    def on_crew_end(self, output: Any = None, **kwargs: Any) -> None:
        self._capture.set_success(True)
        self._capture.__exit__(None, None, None)

    def on_crew_error(self, error: Exception | None = None, **kwargs: Any) -> None:
        self._capture.set_success(False)
        self._capture.__exit__(type(error), error, None)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def get_atir(self):
        """Return the completed ATIRTrace."""
        return self._capture.get_atir()

    def savings_report(self):
        """Return the SavingsReport if guards are active."""
        return self._capture.savings_report()

    def set_quality(self, score: float) -> None:
        self._capture.set_quality(score)


# ---------------------------------------------------------------------------
# Context manager helper
# ---------------------------------------------------------------------------

@contextmanager
def trace_crew(
    agent_name: str,
    task: str = "",
    guards: GuardConfig | None = None,
) -> Generator[RunCoreCrewCallback, None, None]:
    """Context manager that traces a CrewAI ``crew.kickoff()`` call.

    Example::

        with trace_crew("support_crew", task="process queue") as tracer:
            result = crew.kickoff()

        print(tracer.get_atir().aggregates.cost_per_successful_task)
    """
    callback = RunCoreCrewCallback(agent_name=agent_name, task=task, guards=guards)
    try:
        yield callback
        callback.on_crew_end()
    except Exception as exc:
        callback.on_crew_error(exc)
        raise
