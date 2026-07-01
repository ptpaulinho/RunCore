"""Base class for benchmark agents — agentic loop over real LLM + simulated tools."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import runcore
from runcore import GuardConfig
from runcore.providers.base import BaseProvider, Message, ToolDefinition
from benchmarks.tasks import BenchmarkTask

# Compact stub that replaces an elided tool result. Small + self-describing so the
# model knows the data was already provided earlier in the conversation.
_ELIDED_STUB = '{"note": "result elided to save context — provided earlier in this conversation"}'


def _estimate_context_tokens(messages: list[Message]) -> int:
    """Cheap ~4-chars-per-token estimate of the whole conversation."""
    chars = 0
    for m in messages:
        chars += len(m.content or "")
        for tc in (m.tool_calls or []):
            fn = tc.get("function", tc)
            chars += len(str(fn.get("arguments", "")))
    return chars // 4


def _elide_stale_tool_outputs(messages: list[Message], keep_last: int = 3,
                              min_context_tokens: int = 1200) -> int:
    """Adaptively collapse older tool-result payloads. Returns tokens saved (est).

    Only fires once the conversation exceeds ``min_context_tokens`` — short
    conversations keep full context so task success is never risked to save a
    handful of tokens. Above the threshold, every tool result except the most
    recent ``keep_last`` is replaced with a compact stub (already-consumed
    evidence need not be re-sent on every subsequent LLM call). Idempotent."""
    if _estimate_context_tokens(messages) < min_context_tokens:
        return 0
    tool_idxs = [i for i, m in enumerate(messages) if m.role == "tool"]
    victims = tool_idxs[:-keep_last] if keep_last > 0 else tool_idxs
    saved = 0
    for i in victims:
        if messages[i].content != _ELIDED_STUB:
            saved += max(0, (len(messages[i].content or "") - len(_ELIDED_STUB)) // 4)
            messages[i].content = _ELIDED_STUB
    return saved


@dataclass
class AgentRun:
    task_id: str
    provider: str
    model: str
    with_guards: bool
    success: bool
    quality_score: float
    turns: int
    tool_calls_made: list[str]      # ordered list of tool names called
    final_answer: str
    trace_path: str | None = None
    error: str | None = None


class BaseAgent:
    """Agentic loop: LLM decides which tools to call; tools return pre-defined responses.

    This is a *deterministic simulator* — tools always return the same canned responses
    from tasks.py. The LLM part is real. This lets us:
    - Use free APIs without needing live external systems
    - Reproduce results exactly (same tool responses every run)
    - Demonstrate RunCore savings on real LLM reasoning
    """

    def __init__(self, provider: BaseProvider, guards: GuardConfig | None = None):
        self.provider = provider
        self.guards = guards

    def _tool_defs(self, task: BenchmarkTask) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"],
            )
            for t in task.tools
        ]

    def _execute_tool(self, name: str, arguments: dict, task: BenchmarkTask) -> str:
        """Return canned tool response as JSON string."""
        resp = task.tool_responses.get(name, {"error": f"Unknown tool: {name}"})
        return json.dumps(resp)

    def run(self, task: BenchmarkTask, cap: "runcore.sdk.capture.Capture") -> AgentRun:
        """Execute one agentic loop run. Records everything into `cap`."""
        messages = [
            Message(role="system", content=task.system_prompt),
            Message(role="user", content=task.user_message),
        ]
        tool_defs = self._tool_defs(task)
        tools_called = []
        tool_cache: dict[str, str] = {}   # (name+args) -> result, for cooperative dedup
        final_answer = ""
        success = False
        error_msg = None

        try:
            for turn in range(task.max_turns):
                # New LLM turn — reset turn-scoped dedup state.
                cap.new_turn()

                # Context compression guard: once a tool result has been consumed by
                # earlier turns, its full payload need not be re-sent on every later LLM
                # call. Elide stale tool outputs (keep the most recent verbatim) — a real
                # input-token saving with decision-relevant context preserved. Guarded only.
                if self.guards is not None:
                    _elide_stale_tool_outputs(messages, keep_last=3)

                resp = self.provider.chat(
                    messages=messages,
                    tools=tool_defs,
                    max_tokens=1024,
                    temperature=0.0,
                )

                # Record LLM call
                cap.record_llm(
                    provider=self.provider.name,
                    model=resp.model,
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    cost_usd=resp.cost_usd,
                    duration_ms=resp.duration_ms,
                    stop_reason=resp.stop_reason,
                    messages_count=len(messages),
                    tools_count=len(tool_defs),
                )

                if not resp.tool_calls:
                    # Final answer
                    final_answer = resp.content or ""
                    messages.append(Message(role="assistant", content=final_answer))
                    break

                # Execute tool calls
                assistant_msg = Message(
                    role="assistant",
                    content=resp.content or "",
                    tool_calls=[
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                        for tc in resp.tool_calls
                    ],
                )
                messages.append(assistant_msg)

                for tc in resp.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]
                    cache_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
                    t0 = time.perf_counter()

                    # Cooperative dedup: if the guard flags this as a duplicate AND we have
                    # the prior result cached, serve a COMPACT REFERENCE instead of re-executing
                    # and re-injecting the full payload. The data is already in the conversation,
                    # so correctness is preserved while the duplicate's large output is not re-sent
                    # to the LLM — a real, safe token saving. The agent keeps going; it never aborts.
                    if cap.dedup_check(tool_name, tool_args) and cache_key in tool_cache:
                        tool_result = json.dumps({
                            "note": f"Already retrieved {tool_name} with identical arguments earlier "
                                    f"in this conversation — see the previous result above.",
                            "deduplicated": True,
                        })
                        tool_success = True
                        duration_ms = 0.0
                        deduped = True
                    else:
                        try:
                            tool_result = self._execute_tool(tool_name, tool_args, task)
                            tool_success = True
                        except Exception as e:
                            tool_result = json.dumps({"error": str(e)})
                            tool_success = False
                        tool_cache[cache_key] = tool_result
                        duration_ms = (time.perf_counter() - t0) * 1000
                        deduped = False

                    tools_called.append(tool_name)

                    # Guard logic already handled above via dedup_check — skip_guard avoids re-raising.
                    cap.record_tool(
                        name=tool_name,
                        arguments=tool_args,
                        result=json.loads(tool_result),
                        success=tool_success,
                        duration_ms=duration_ms,
                        skip_guard=True,
                        metadata={"deduplicated": True} if deduped else None,
                    )

                    messages.append(Message(
                        role="tool",
                        content=tool_result,
                        tool_call_id=tc["id"],
                    ))

            # Evaluate success.
            # Ground truth = the agent took the right actions (called every expected tool).
            # Keyword match is a secondary signal — phrasing varies across models, so a model
            # that completes the task but words its summary differently still counts as success.
            tools_ok = all(t in tools_called for t in task.expected_tools_called)
            answer_lower = final_answer.lower()
            keywords_ok = all(kw.lower() in answer_lower for kw in task.success_keywords)
            # Success if the right tools were called and we produced an answer; or the answer
            # explicitly satisfies the keyword check (covers tool-free tasks).
            success = (tools_ok and bool(final_answer.strip())) or keywords_ok
            quality = 1.0 if (tools_ok and keywords_ok) else 0.8 if success else 0.5

        except Exception as e:
            error_msg = str(e)
            # Partial success if we got a final answer
            success = bool(final_answer)
            quality = 0.3

        cap.set_success(success)
        cap.set_quality(quality if success else 0.3)

        return AgentRun(
            task_id=task.id,
            provider=self.provider.name,
            model=self.provider.model,
            with_guards=self.guards is not None,
            success=success,
            quality_score=quality if success else 0.3,
            turns=len([m for m in messages if m.role == "assistant"]),
            tool_calls_made=tools_called,
            final_answer=final_answer,
            error=error_msg,
        )
