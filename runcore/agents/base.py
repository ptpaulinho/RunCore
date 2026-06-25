"""Base agent class for RunCore simulated agents."""
from __future__ import annotations

import json
import random
import time
from abc import ABC, abstractmethod
from typing import Any

from runcore.core.models import AgentTrace, LLMCall, ToolCall
from runcore.tools.registry import ToolSchema
from runcore.trace.collector import TraceCollector
from runcore.trace.tokens import count_tokens, estimate_prompt_tokens


class BaseAgent(ABC):
    name: str = "base"
    model: str = "claude-3-5-sonnet-20241022"

    _system_prompt: str = "You are a helpful AI assistant."

    def __init__(self, optimization=None, seed: int | None = 42) -> None:
        self.tools: list[ToolSchema] = []
        self.collector = TraceCollector()
        self._contexts: dict[str, list[dict[str, str]]] = {}
        self._optimization = optimization  # Optional[OptimizationProfile]
        # Per-run dedup tracking for runtime skip logic
        self._run_seen_sigs: dict[str, set[str]] = {}
        # Per-run quality signals for real scoring
        self._run_signals: dict[str, dict[str, Any]] = {}
        # Seed random for reproducible benchmark results
        if seed is not None:
            import random
            random.seed(seed)

    @abstractmethod
    def run(self, task: str) -> AgentTrace:
        ...

    # ------------------------------------------------------------------
    # Context accumulation
    # ------------------------------------------------------------------

    def _init_context(self, run_id: str, task: str) -> None:
        self._contexts[run_id] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task},
        ]
        self._run_seen_sigs[run_id] = set()
        self._run_signals[run_id] = {
            "tools_called": [],   # (name, success) tuples in order
            "task": task,
        }

    def _append_context(self, run_id: str, role: str, content: str) -> None:
        self._contexts.setdefault(run_id, []).append({"role": role, "content": content})

    def _context_tokens(self, run_id: str) -> int:
        return estimate_prompt_tokens(self._contexts.get(run_id, []))

    def _completion_tokens(self, text: str) -> int:
        return count_tokens(text, self.model)

    def _apply_context_compression(self, run_id: str) -> None:
        """Run the ContextCompiler on the accumulated context in-place."""
        messages = self._contexts.get(run_id)
        if not messages or len(messages) < 4:
            return
        try:
            from runcore.context.compiler import ContextCompiler
            compiler = ContextCompiler()
            signals = self._run_signals.get(run_id, {})
            result = compiler.compile(messages, task=signals.get("task", ""))
            compiled = result.get("compiled_messages")
            if compiled and len(compiled) >= 2:
                self._contexts[run_id] = compiled
        except Exception:
            pass  # never break a run due to compression error

    # ------------------------------------------------------------------
    # Simulation helpers
    # ------------------------------------------------------------------

    def _simulate_llm_call(
        self,
        run_id: str,
        completion_text: str,
        latency_ms: float | None = None,
    ) -> LLMCall:
        """Record an LLM call, counting tokens from the real accumulated context.

        If an OptimizationProfile is active:
        - Context is compressed before counting tokens (real compression savings)
        - Schema token savings are subtracted from prompt tokens
        """
        opt = self._optimization

        # Apply real context compression if enabled
        if opt and opt.compress_context:
            self._apply_context_compression(run_id)

        prompt_tokens = self._context_tokens(run_id)

        # Apply schema compression savings (real per-call savings)
        if opt and opt.schema_token_savings_per_call > 0:
            prompt_tokens = max(0, prompt_tokens - opt.schema_token_savings_per_call)

        completion_tokens = self._completion_tokens(completion_text)
        self._append_context(run_id, "assistant", completion_text)

        if latency_ms is None:
            latency_ms = (prompt_tokens + completion_tokens) * 0.10 + random.uniform(80, 300)

        return self.collector.record_llm_call(
            run_id, self.model, prompt_tokens, completion_tokens, latency_ms
        )

    def _simulate_tool_call(
        self,
        run_id: str,
        name: str,
        args: dict[str, Any],
        result: Any,
        success: bool = True,
        latency_ms: float | None = None,
    ) -> ToolCall | None:
        """Record a tool call, injecting its result into the conversation context.

        Returns None (and records nothing) when the call is skipped because:
        - Its signature is a known global duplicate from the optimization profile
        - Runtime dedup is enabled and the same signature was already called in this run
        """
        sig = f"{name}:{json.dumps(args, sort_keys=True)}"
        opt = self._optimization

        if opt is not None:
            # Check global skip set (high-confidence duplicates from baseline)
            if sig in opt.global_skip_signatures:
                return None

            # Runtime dedup: skip if seen in this run already
            if opt.runtime_dedup:
                seen = self._run_seen_sigs.setdefault(run_id, set())
                if sig in seen:
                    return None
                seen.add(sig)

        # Record quality signal regardless of optimization
        signals = self._run_signals.setdefault(run_id, {"tools_called": [], "task": ""})
        signals["tools_called"].append((name, success))

        if latency_ms is None:
            latency_ms = random.uniform(50, 400)

        tool_text = f"Tool: {name}({json.dumps(args)})\nResult: {json.dumps(result, default=str)}"
        self._append_context(run_id, "user", tool_text)
        tokens_used = count_tokens(tool_text, self.model)

        return self.collector.record_tool_call(
            run_id, name, args, result, success, latency_ms, tokens_used=tokens_used
        )

    # ------------------------------------------------------------------
    # Quality scoring
    # ------------------------------------------------------------------

    def _compute_quality(self, run_id: str) -> float:
        """Compute a real quality score based on what happened during the run.

        Subclasses override this to use task-specific criteria.
        Default: penalise duplicate tool calls and failures.
        """
        signals = self._run_signals.get(run_id, {})
        calls = signals.get("tools_called", [])
        if not calls:
            return 0.5

        total = len(calls)
        successes = sum(1 for _, ok in calls if ok)
        unique_names = len({n for n, _ in calls})

        success_rate = successes / total
        dup_penalty = max(0.0, (total - unique_names) / total) * 0.3

        return max(0.1, round(success_rate - dup_penalty, 3))
