"""LangChain callback handler that records to an active Capture context."""
from __future__ import annotations

import time
from typing import Any, Union
from uuid import UUID

from runcore.sdk import context as _ctx

try:
    from langchain_core.callbacks.base import BaseCallbackHandler
    from langchain_core.outputs import LLMResult
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False
    BaseCallbackHandler = object  # type: ignore
    LLMResult = Any  # type: ignore


class RunCoreLangChainCallback(BaseCallbackHandler):
    """LangChain callback that forwards LLM and tool events to the active Capture.

    Usage::

        with runcore.capture("my_chain", framework="langchain") as tracer:
            chain = MyChain(callbacks=[RunCoreLangChainCallback()])
            result = chain.run("some task")

        trace = tracer.get_atir()
    """

    def __init__(self) -> None:
        if not _LANGCHAIN_AVAILABLE:
            raise ImportError(
                "langchain-core is not installed. "
                "Run: pip install langchain-core"
            )
        super().__init__()
        self._llm_start_times: dict[str, float] = {}
        self._tool_start_times: dict[str, float] = {}

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
        self._llm_start_times[str(run_id)] = time.perf_counter()

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        capture = _ctx.current()
        if capture is None:
            return

        elapsed = (time.perf_counter() - self._llm_start_times.pop(str(run_id), time.perf_counter())) * 1000

        # Extract token usage from LangChain LLMResult
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0
        model = "unknown"

        for generations in response.generations:
            for gen in generations:
                llm_output = getattr(gen, "generation_info", {}) or {}
                usage = llm_output.get("usage", {}) or {}
                input_tokens += usage.get("prompt_tokens", usage.get("input_tokens", 0))
                output_tokens += usage.get("completion_tokens", usage.get("output_tokens", 0))

        if response.llm_output:
            token_usage = response.llm_output.get("token_usage", {}) or {}
            if not input_tokens:
                input_tokens = token_usage.get("prompt_tokens", 0)
            if not output_tokens:
                output_tokens = token_usage.get("completion_tokens", 0)
            model = response.llm_output.get("model_name", "unknown")

        try:
            from runcore.trace.cost import calculate_llm_cost
            cost_usd = calculate_llm_cost(model, input_tokens, output_tokens)
        except Exception:
            pass

        capture.record_llm(
            provider="langchain",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            duration_ms=elapsed,
        )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._llm_start_times.pop(str(run_id), None)
        capture = _ctx.current()
        if capture:
            capture.set_success(False)

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
        self._tool_start_times[str(run_id)] = time.perf_counter()

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        capture = _ctx.current()
        if capture is None:
            return

        elapsed = (time.perf_counter() - self._tool_start_times.pop(str(run_id), time.perf_counter())) * 1000
        tool_name = name or kwargs.get("serialized", {}).get("name", "unknown_tool")

        capture.record_tool(
            name=tool_name,
            arguments={},
            result=str(output)[:200],
            success=True,
            duration_ms=elapsed,
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        capture = _ctx.current()
        if capture is None:
            return

        elapsed = (time.perf_counter() - self._tool_start_times.pop(str(run_id), time.perf_counter())) * 1000
        tool_name = name or "unknown_tool"

        capture.record_tool(
            name=tool_name,
            arguments={},
            result=str(error),
            success=False,
            duration_ms=elapsed,
        )
