"""RunCore adapter for AutoGen (Microsoft).

Wraps ``ConversableAgent.initiate_chat()`` (AutoGen v0.2.x) and
``AssistantAgent`` / ``UserProxyAgent`` patterns to capture every
message exchange and function call as ATIR spans.

Usage::

    from autogen import AssistantAgent, UserProxyAgent
    from runcore.sdk.adapters.autogen import RunCoreAutoGenTracer

    tracer = RunCoreAutoGenTracer(agent_name="autogen_assistant", task="write tests")

    assistant = tracer.wrap_agent(AssistantAgent("assistant", llm_config=...))
    user_proxy = UserProxyAgent("user_proxy", ...)

    with tracer:
        user_proxy.initiate_chat(assistant, message="Write unit tests for foo.py")

    trace = tracer.get_atir()
    print(f"CpST: ${trace.aggregates.cost_per_successful_task:.5f}")

    # Alternative — wrap initiate_chat directly
    tracer = RunCoreAutoGenTracer(agent_name="my_agent", task="code review")
    result = tracer.initiate_chat(user_proxy, assistant, message="Review this PR")
    trace = tracer.get_atir()

AutoGen is NOT a required dependency of runcore.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from runcore.sdk.capture import Capture
from runcore.sdk.guards import GuardConfig


class RunCoreAutoGenTracer:
    """Traces an AutoGen multi-agent conversation into an ATIR trace.

    Records:
    - Each message exchange as a ToolSpan (tool name = agent role)
    - Each function/tool call made by an agent as a ToolSpan
    - LLM usage extracted from AutoGen's cost tracking as LLMSpans

    Parameters
    ----------
    agent_name:
        Label for the conversation in reports.
    task:
        Description of the objective.
    framework:
        Framework tag (default: ``"autogen"``).
    guards:
        Optional :class:`~runcore.sdk.guards.GuardConfig` for runtime guards.
    """

    def __init__(
        self,
        agent_name: str,
        task: str = "",
        framework: str = "autogen",
        guards: GuardConfig | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.task = task
        self.framework = framework
        self._guards = guards
        self._capture: Capture | None = None
        self._conversation_start: float = 0.0

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "RunCoreAutoGenTracer":
        self._capture = Capture(
            agent_name=self.agent_name,
            task=self.task,
            framework=self.framework,
            guards=self._guards,
        )
        self._capture.__enter__()
        self._conversation_start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._capture:
            if exc_type is not None:
                self._capture.set_success(False)
            self._capture.__exit__(exc_type, exc_val, exc_tb)
        return False

    # ------------------------------------------------------------------
    # Agent wrapping
    # ------------------------------------------------------------------

    def wrap_agent(self, agent: Any) -> Any:
        """Return a wrapped AutoGen agent that records all messages.

        Works with ``ConversableAgent``, ``AssistantAgent``,
        ``UserProxyAgent``, and any subclass.
        """
        return _WrappedAutoGenAgent(agent, self)

    # ------------------------------------------------------------------
    # Direct initiate_chat wrapper
    # ------------------------------------------------------------------

    def initiate_chat(
        self,
        initiator: Any,
        recipient: Any,
        message: str,
        **kwargs: Any,
    ) -> Any:
        """Wrap ``initiator.initiate_chat(recipient, message=message)``.

        Starts a capture, runs the chat, records usage, and finalises.
        """
        self._capture = Capture(
            agent_name=self.agent_name,
            task=self.task or message[:100],
            framework=self.framework,
            guards=self._guards,
        )
        self._capture.__enter__()
        self._conversation_start = time.perf_counter()

        success = True
        try:
            result = initiator.initiate_chat(recipient, message=message, **kwargs)
            self._record_usage_from_result(initiator, recipient, result)
            return result
        except Exception:
            success = False
            raise
        finally:
            duration = (time.perf_counter() - self._conversation_start) * 1000
            self._capture.record_tool(
                name="conversation",
                arguments={"message": message[:200]},
                result=None,
                success=success,
                duration_ms=duration,
                input_tokens=max(1, len(message) // 4),
                metadata={"initiator": getattr(initiator, "name", "initiator"),
                           "recipient": getattr(recipient, "name", "recipient")},
            )
            self._capture.set_success(success)
            self._capture.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # Recording helpers — called by _WrappedAutoGenAgent
    # ------------------------------------------------------------------

    def record_message(
        self,
        sender_name: str,
        recipient_name: str,
        message: str | dict,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        """Record a single message exchange as a ToolSpan."""
        if self._capture is None:
            return

        msg_str = message if isinstance(message, str) else str(message)
        try:
            self._capture.record_tool(
                name=f"message:{sender_name}→{recipient_name}",
                arguments={"content": msg_str[:300]},
                result=None,
                success=success,
                duration_ms=duration_ms,
                input_tokens=max(1, len(msg_str) // 4),
                metadata={"sender": sender_name, "recipient": recipient_name},
            )
        except Exception:
            pass

    def record_function_call(
        self,
        function_name: str,
        arguments: dict,
        result: Any,
        duration_ms: float,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Record a function / tool call made by an AutoGen agent."""
        if self._capture is None:
            return
        try:
            self._capture.record_tool(
                name=function_name,
                arguments=arguments,
                result=result,
                success=success,
                duration_ms=duration_ms,
                input_tokens=max(1, len(str(arguments)) // 4),
                metadata={"error": error} if error else {},
            )
        except Exception:
            pass

    def record_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        duration_ms: float,
    ) -> None:
        """Record an LLM API call made by an AutoGen agent."""
        if self._capture:
            self._capture.record_llm(
                provider="autogen",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
            )

    def _record_usage_from_result(
        self,
        initiator: Any,
        recipient: Any,
        result: Any,
    ) -> None:
        """Extract cost/usage from AutoGen's built-in cost tracking."""
        for agent in (initiator, recipient):
            try:
                # AutoGen stores cost in agent.client.total_usage_summary
                summary = getattr(agent, "client", None)
                if summary is None:
                    continue
                usage = getattr(summary, "total_usage_summary", None)
                if not usage:
                    continue
                for model, stats in usage.items():
                    if not isinstance(stats, dict):
                        continue
                    input_tok  = stats.get("prompt_tokens", 0)
                    output_tok = stats.get("completion_tokens", 0)
                    cost       = stats.get("cost", (input_tok + output_tok) * 3e-6)
                    if input_tok > 0 or output_tok > 0:
                        self._capture.record_llm(
                            provider="autogen",
                            model=model,
                            input_tokens=input_tok,
                            output_tokens=output_tok,
                            cost_usd=cost,
                            duration_ms=0.0,
                        )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def get_atir(self):
        """Return the completed ATIRTrace."""
        if self._capture is None:
            raise RuntimeError("No active capture — run a conversation first.")
        return self._capture.get_atir()

    def savings_report(self):
        """Return the SavingsReport if guards are active."""
        return self._capture.savings_report() if self._capture else None

    def set_quality(self, score: float) -> None:
        if self._capture:
            self._capture.set_quality(score)


class _WrappedAutoGenAgent:
    """Transparent proxy around an AutoGen ConversableAgent.

    Intercepts ``generate_reply`` (the core method that produces a response)
    and ``execute_function`` to record them as spans.
    """

    def __init__(self, agent: Any, tracer: RunCoreAutoGenTracer) -> None:
        self._agent = agent
        self._tracer = tracer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)

    def generate_reply(
        self,
        messages: list | None = None,
        sender: Any = None,
        **kwargs: Any,
    ) -> str | dict | None:
        t0 = time.perf_counter()
        sender_name = getattr(sender, "name", "unknown") if sender else "unknown"
        agent_name  = getattr(self._agent, "name", "agent")

        try:
            reply = self._agent.generate_reply(messages=messages, sender=sender, **kwargs)
            duration = (time.perf_counter() - t0) * 1000
            self._tracer.record_message(
                sender_name=sender_name,
                recipient_name=agent_name,
                message=reply or "",
                duration_ms=duration,
                success=True,
            )
            return reply
        except Exception as exc:
            duration = (time.perf_counter() - t0) * 1000
            self._tracer.record_message(
                sender_name=sender_name,
                recipient_name=agent_name,
                message=f"ERROR: {exc}",
                duration_ms=duration,
                success=False,
            )
            raise

    def execute_function(
        self,
        func_call: dict,
        **kwargs: Any,
    ) -> tuple[bool, dict]:
        t0 = time.perf_counter()
        fn_name = func_call.get("name", "function")
        fn_args = func_call.get("arguments", {})
        if isinstance(fn_args, str):
            import json
            try:
                fn_args = json.loads(fn_args)
            except Exception:
                fn_args = {"raw": fn_args}

        try:
            success, result = self._agent.execute_function(func_call, **kwargs)
            duration = (time.perf_counter() - t0) * 1000
            self._tracer.record_function_call(
                function_name=fn_name,
                arguments=fn_args,
                result=result,
                duration_ms=duration,
                success=success,
            )
            return success, result
        except Exception as exc:
            duration = (time.perf_counter() - t0) * 1000
            self._tracer.record_function_call(
                function_name=fn_name,
                arguments=fn_args,
                result=None,
                duration_ms=duration,
                success=False,
                error=str(exc),
            )
            raise
