"""RunCore adapter for LangChain / LangChain Expression Language (LCEL).

Two integration modes:

**Mode 1 — Tracer (owns the Capture, consistent with other adapters):**

    from runcore.sdk.adapters.langchain import RunCoreLangChainTracer

    tracer = RunCoreLangChainTracer(agent_name="my_chain", task="summarise")

    # Option A: context manager
    with tracer:
        result = chain.invoke({"input": "..."}, config={"callbacks": [tracer.callback]})

    trace = tracer.get_atir()
    print(f"CpST: ${trace.aggregates.cost_per_successful_task:.5f}")

    # Option B: wrap a Runnable (LCEL)
    wrapped = tracer.wrap(chain)
    result  = wrapped.invoke({"input": "..."})
    trace   = tracer.get_atir()

**Mode 2 — Callback (attaches to an existing runcore.capture() context):**

    import runcore
    from runcore.sdk.adapters.langchain import RunCoreLangChainCallback

    with runcore.capture("my_chain", framework="langchain") as tracer:
        chain = MyChain(callbacks=[RunCoreLangChainCallback()])
        result = chain.run("some task")

    trace = tracer.get_atir()

LangChain is NOT a required dependency of runcore.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Generator
from uuid import UUID

from runcore.sdk.capture import Capture
from runcore.sdk.guards import GuardConfig
from runcore.sdk import context as _ctx

try:
    from langchain_core.callbacks.base import BaseCallbackHandler
    from langchain_core.outputs import LLMResult
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False
    BaseCallbackHandler = object  # type: ignore
    LLMResult = Any              # type: ignore


# ---------------------------------------------------------------------------
# Internal shared callback — used by both Tracer and standalone Callback
# ---------------------------------------------------------------------------

class _RunCoreHandler(BaseCallbackHandler if _LANGCHAIN_AVAILABLE else object):  # type: ignore
    """Core LangChain callback that writes spans to a Capture instance."""

    def __init__(self, capture: Capture) -> None:
        if _LANGCHAIN_AVAILABLE:
            super().__init__()
        self._capture = capture
        self._llm_starts:  dict[str, float] = {}
        self._tool_starts: dict[str, float] = {}
        self._chain_starts: dict[str, float] = {}

    # ------------------------------------------------------------------
    # LLM hooks
    # ------------------------------------------------------------------

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._llm_starts[str(run_id)] = time.perf_counter()

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        elapsed = (time.perf_counter() - self._llm_starts.pop(str(run_id), time.perf_counter())) * 1000

        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0
        model = "unknown"

        # Extract token usage from LangChain LLMResult
        try:
            for generations in response.generations:
                for gen in generations:
                    info = getattr(gen, "generation_info", {}) or {}
                    usage = info.get("usage", {}) or {}
                    input_tokens  += usage.get("prompt_tokens",     usage.get("input_tokens",  0))
                    output_tokens += usage.get("completion_tokens", usage.get("output_tokens", 0))

            if response.llm_output:
                tu = response.llm_output.get("token_usage", {}) or {}
                if not input_tokens:
                    input_tokens = tu.get("prompt_tokens", 0)
                if not output_tokens:
                    output_tokens = tu.get("completion_tokens", 0)
                model = response.llm_output.get("model_name", "unknown")
        except Exception:
            pass

        try:
            from runcore.trace.cost import calculate_llm_cost
            cost_usd = calculate_llm_cost(model, input_tokens, output_tokens)
        except Exception:
            cost_usd = (input_tokens + output_tokens) * 3e-6

        if input_tokens > 0 or output_tokens > 0:
            self._capture.record_llm(
                provider="langchain",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=elapsed,
            )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._llm_starts.pop(str(run_id), None)
        self._capture.set_success(False)

    # ------------------------------------------------------------------
    # Tool hooks
    # ------------------------------------------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._tool_starts[str(run_id)] = time.perf_counter()

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        elapsed = (time.perf_counter() - self._tool_starts.pop(str(run_id), time.perf_counter())) * 1000
        tool_name = name or kwargs.get("serialized", {}).get("name", "tool")

        try:
            self._capture.record_tool(
                name=tool_name,
                arguments={},
                result=str(output)[:300],
                success=True,
                duration_ms=elapsed,
            )
        except Exception:
            pass

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        elapsed = (time.perf_counter() - self._tool_starts.pop(str(run_id), time.perf_counter())) * 1000
        tool_name = name or "tool"

        try:
            self._capture.record_tool(
                name=tool_name,
                arguments={},
                result=None,
                success=False,
                duration_ms=elapsed,
                metadata={"error": str(error)},
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Chain hooks (LCEL Runnable chains)
    # ------------------------------------------------------------------

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._chain_starts[str(run_id)] = time.perf_counter()

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._chain_starts.pop(str(run_id), None)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._chain_starts.pop(str(run_id), None)
        self._capture.set_success(False)


# ---------------------------------------------------------------------------
# Mode 1: RunCoreLangChainTracer — owns the Capture
# ---------------------------------------------------------------------------

class RunCoreLangChainTracer:
    """Traces a LangChain chain or LCEL Runnable into an ATIR trace.

    Consistent with :class:`RunCoreLangGraphTracer`, :class:`RunCoreCrewCallback`,
    and :class:`RunCoreAutoGenTracer` — owns its Capture and exposes
    ``get_atir()`` / ``savings_report()``.

    Parameters
    ----------
    agent_name:
        Label for the chain in reports.
    task:
        Description of the task being performed.
    framework:
        Framework tag (default: ``"langchain"``).
    guards:
        Optional :class:`~runcore.sdk.guards.GuardConfig` for runtime guards.
    """

    def __init__(
        self,
        agent_name: str,
        task: str = "",
        framework: str = "langchain",
        guards: GuardConfig | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.task = task
        self.framework = framework
        self._guards = guards
        self._capture: Capture | None = None
        self._handler: _RunCoreHandler | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "RunCoreLangChainTracer":
        self._capture = Capture(
            agent_name=self.agent_name,
            task=self.task,
            framework=self.framework,
            guards=self._guards,
        )
        self._capture.__enter__()
        self._handler = _RunCoreHandler(self._capture)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._capture:
            if exc_type is not None:
                self._capture.set_success(False)
            self._capture.__exit__(exc_type, exc_val, exc_tb)
        return False

    # ------------------------------------------------------------------
    # callback property — pass to chain.invoke(config={"callbacks": [tracer.callback]})
    # ------------------------------------------------------------------

    @property
    def callback(self) -> _RunCoreHandler:
        """Return the LangChain callback handler for this tracer.

        Use inside the context manager::

            with tracer:
                result = chain.invoke({...}, config={"callbacks": [tracer.callback]})
        """
        if not _LANGCHAIN_AVAILABLE:
            raise ImportError(
                "langchain-core is not installed. Run: pip install langchain-core"
            )
        if self._handler is None:
            raise RuntimeError("Use RunCoreLangChainTracer as a context manager first.")
        return self._handler

    # ------------------------------------------------------------------
    # wrap — transparent LCEL Runnable proxy
    # ------------------------------------------------------------------

    def wrap(self, runnable: Any) -> "_WrappedRunnable":
        """Return a wrapped LCEL Runnable that records all calls.

        Works with any LangChain ``Runnable`` (chains, agents, retrievers).

        Example::

            tracer = RunCoreLangChainTracer("my_chain", task="qa")
            wrapped = tracer.wrap(chain)
            result  = wrapped.invoke({"question": "..."})
            trace   = tracer.get_atir()
        """
        return _WrappedRunnable(runnable, self)

    # ------------------------------------------------------------------
    # Manual recording (lower-level, for custom integrations)
    # ------------------------------------------------------------------

    def record_llm(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        duration_ms: float,
        provider: str = "langchain",
    ) -> None:
        if self._capture:
            self._capture.record_llm(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
            )

    def record_tool(
        self,
        name: str,
        arguments: dict,
        result: Any,
        success: bool = True,
        duration_ms: float = 0.0,
    ) -> None:
        if self._capture:
            try:
                self._capture.record_tool(
                    name=name,
                    arguments=arguments,
                    result=result,
                    success=success,
                    duration_ms=duration_ms,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def get_atir(self):
        """Return the completed ATIRTrace."""
        if self._capture is None:
            raise RuntimeError("No active capture — use as a context manager or call wrap() first.")
        return self._capture.get_atir()

    def savings_report(self):
        """Return the SavingsReport if guards are active."""
        return self._capture.savings_report() if self._capture else None

    def set_quality(self, score: float) -> None:
        if self._capture:
            self._capture.set_quality(score)

    def set_success(self, success: bool) -> None:
        if self._capture:
            self._capture.set_success(success)


# ---------------------------------------------------------------------------
# _WrappedRunnable — transparent LCEL proxy
# ---------------------------------------------------------------------------

class _WrappedRunnable:
    """Transparent proxy around a LangChain Runnable that records execution."""

    def __init__(self, runnable: Any, tracer: RunCoreLangChainTracer) -> None:
        self._runnable = runnable
        self._tracer = tracer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runnable, name)

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        self._tracer._capture = Capture(
            agent_name=self._tracer.agent_name,
            task=self._tracer.task,
            framework=self._tracer.framework,
            guards=self._tracer._guards,
        )
        self._tracer._capture.__enter__()
        self._tracer._handler = _RunCoreHandler(self._tracer._capture)

        t0 = time.perf_counter()
        success = True
        try:
            # Inject our callback into the run config
            if config is None:
                config = {}
            existing_cbs = list(config.get("callbacks", []) or [])
            existing_cbs.append(self._tracer._handler)
            config = {**config, "callbacks": existing_cbs}

            result = self._runnable.invoke(input, config, **kwargs)
            return result
        except Exception:
            success = False
            raise
        finally:
            duration = (time.perf_counter() - t0) * 1000
            self._tracer._capture.set_success(success)
            self._tracer._capture.__exit__(None, None, None)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        self._tracer._capture = Capture(
            agent_name=self._tracer.agent_name,
            task=self._tracer.task,
            framework=self._tracer.framework,
            guards=self._tracer._guards,
        )
        self._tracer._capture.__enter__()
        self._tracer._handler = _RunCoreHandler(self._tracer._capture)

        t0 = time.perf_counter()
        success = True
        try:
            if config is None:
                config = {}
            existing_cbs = list(config.get("callbacks", []) or [])
            existing_cbs.append(self._tracer._handler)
            config = {**config, "callbacks": existing_cbs}

            result = await self._runnable.ainvoke(input, config, **kwargs)
            return result
        except Exception:
            success = False
            raise
        finally:
            self._tracer._capture.set_success(success)
            self._tracer._capture.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Mode 2: RunCoreLangChainCallback — attaches to existing capture() context
# ---------------------------------------------------------------------------

class RunCoreLangChainCallback(BaseCallbackHandler if _LANGCHAIN_AVAILABLE else object):  # type: ignore
    """LangChain callback that forwards events to the **active** runcore.capture() context.

    Use this when you already manage a ``runcore.capture()`` context manager
    and just want to attach LangChain event recording to it::

        import runcore
        from runcore.sdk.adapters.langchain import RunCoreLangChainCallback

        with runcore.capture("my_chain", framework="langchain") as tracer:
            chain = MyChain(callbacks=[RunCoreLangChainCallback()])
            result = chain.run("some task")

        trace = tracer.get_atir()

    If no active capture context exists when events fire, they are silently dropped.
    """

    def __init__(self) -> None:
        super().__init__()
        self._llm_starts:  dict[str, float] = {}
        self._tool_starts: dict[str, float] = {}

    def _get_handler(self) -> _RunCoreHandler | None:
        cap = _ctx.current()
        if cap is None:
            return None
        # Lazily attach a handler bound to the current capture
        key = id(cap)
        if not hasattr(self, "_handlers"):
            self._handlers: dict[int, _RunCoreHandler] = {}
        if key not in self._handlers:
            self._handlers[key] = _RunCoreHandler(cap)
        return self._handlers[key]

    def on_llm_start(self, serialized, prompts, *, run_id: UUID, **kw) -> None:
        h = self._get_handler()
        if h:
            h.on_llm_start(serialized, prompts, run_id=run_id, **kw)

    def on_llm_end(self, response, *, run_id: UUID, **kw) -> None:
        h = self._get_handler()
        if h:
            h.on_llm_end(response, run_id=run_id, **kw)

    def on_llm_error(self, error, *, run_id: UUID, **kw) -> None:
        h = self._get_handler()
        if h:
            h.on_llm_error(error, run_id=run_id, **kw)

    def on_tool_start(self, serialized, input_str, *, run_id: UUID, **kw) -> None:
        h = self._get_handler()
        if h:
            h.on_tool_start(serialized, input_str, run_id=run_id, **kw)

    def on_tool_end(self, output, *, run_id: UUID, name=None, **kw) -> None:
        h = self._get_handler()
        if h:
            h.on_tool_end(output, run_id=run_id, name=name, **kw)

    def on_tool_error(self, error, *, run_id: UUID, name=None, **kw) -> None:
        h = self._get_handler()
        if h:
            h.on_tool_error(error, run_id=run_id, name=name, **kw)

    def on_chain_start(self, serialized, inputs, *, run_id: UUID, **kw) -> None:
        h = self._get_handler()
        if h:
            h.on_chain_start(serialized, inputs, run_id=run_id, **kw)

    def on_chain_end(self, outputs, *, run_id: UUID, **kw) -> None:
        h = self._get_handler()
        if h:
            h.on_chain_end(outputs, run_id=run_id, **kw)

    def on_chain_error(self, error, *, run_id: UUID, **kw) -> None:
        h = self._get_handler()
        if h:
            h.on_chain_error(error, run_id=run_id, **kw)


# ---------------------------------------------------------------------------
# trace_chain — convenience context manager (mirrors trace_crew)
# ---------------------------------------------------------------------------

@contextmanager
def trace_chain(
    agent_name: str,
    task: str = "",
    guards: GuardConfig | None = None,
) -> Generator[RunCoreLangChainTracer, None, None]:
    """Context manager that traces a LangChain chain execution.

    Example::

        with trace_chain("support_chain", task="answer question") as tracer:
            result = chain.invoke(
                {"question": "..."},
                config={"callbacks": [tracer.callback]}
            )

        print(tracer.get_atir().aggregates.cost_per_successful_task)
    """
    tracer = RunCoreLangChainTracer(agent_name=agent_name, task=task, guards=guards)
    with tracer:
        try:
            yield tracer
        except Exception:
            tracer.set_success(False)
            raise
