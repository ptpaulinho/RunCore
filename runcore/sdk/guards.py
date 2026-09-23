"""Runtime guards — active optimization applied during agent execution.

Guards intercept tool calls and LLM inputs *before* they happen, blocking
waste in real time rather than just diagnosing it after the fact.

Usage::

    with runcore.capture("my_agent", guards=GuardConfig()) as cap:
        # Duplicate tool calls are blocked automatically.
        # Loop risk is monitored; raises LoopBreakError if threshold exceeded.
        # Context is compressed before each LLM call if tokens > threshold.
        result = agent.run(task)

    print(cap.savings_report())
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DuplicateToolCallError(RuntimeError):
    """Raised when a guard blocks a duplicate tool call."""
    def __init__(self, name: str, arguments: dict) -> None:
        self.tool_name = name
        self.arguments = arguments
        super().__init__(
            f"[RunCore] Duplicate tool call blocked: {name}({json.dumps(arguments)})"
        )


class LoopBreakError(RuntimeError):
    """Raised when loop risk exceeds the configured threshold."""
    def __init__(self, score: float, threshold: float) -> None:
        self.score = score
        self.threshold = threshold
        super().__init__(
            f"[RunCore] Loop break triggered: risk={score:.3f} > threshold={threshold:.3f}"
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class GuardConfig:
    """Configuration for runtime guards.

    All guards are opt-in.  Create a ``GuardConfig`` and pass it to
    :class:`~runcore.sdk.capture.Capture` via ``guards=``.

    Attributes:
        dedup_enabled: Block exact-duplicate tool calls (same name + args).
        dedup_scope: ``"turn"`` (per LLM turn) or ``"session"`` (entire capture).
        loop_break_enabled: Raise LoopBreakError when loop risk exceeds threshold.
        loop_break_threshold: Loop Risk Score above which execution is stopped.
        loop_break_min_calls: Minimum tool calls before loop detection activates.
        context_compression_enabled: Auto-compress messages before LLM calls.
        context_compression_token_threshold: Only compress when input_tokens exceeds this.
        context_compression_dedup_threshold: Semantic similarity threshold for dedup.
    """
    # Dedup guard
    dedup_enabled: bool = True
    # "session" catches cross-turn repetition ("agent forgot it already called this"),
    # the dominant waste pattern. "turn" only catches repeats within one LLM response.
    dedup_scope: str = "session"  # "turn" | "session"

    # Loop break guard
    loop_break_enabled: bool = True
    loop_break_threshold: float = 0.40
    loop_break_min_calls: int = 4

    # Context compression guard
    context_compression_enabled: bool = True
    context_compression_token_threshold: int = 800
    context_compression_dedup_threshold: float = 0.85


# ---------------------------------------------------------------------------
# Savings tracking
# ---------------------------------------------------------------------------

@dataclass
class SavingsReport:
    """Accumulated savings from all guards during a Capture session."""

    # Dedup
    blocked_tool_calls: int = 0
    blocked_tool_calls_tokens: int = 0
    blocked_tool_calls_cost_usd: float = 0.0

    # Context compression
    compression_runs: int = 0
    tokens_saved_compression: int = 0
    cost_saved_compression_usd: float = 0.0

    # Loop break
    loop_breaks: int = 0

    # Pricing constant (blended avg across providers)
    _TOKEN_COST: float = field(default=3e-6, repr=False)

    @property
    def total_tokens_saved(self) -> int:
        return self.blocked_tool_calls_tokens + self.tokens_saved_compression

    @property
    def total_cost_saved_usd(self) -> float:
        return self.blocked_tool_calls_cost_usd + self.cost_saved_compression_usd

    def to_dict(self) -> dict:
        return {
            "blocked_tool_calls": self.blocked_tool_calls,
            "tokens_saved": self.total_tokens_saved,
            "cost_saved_usd": round(self.total_cost_saved_usd, 6),
            "compression_runs": self.compression_runs,
            "tokens_saved_compression": self.tokens_saved_compression,
            "loop_breaks": self.loop_breaks,
        }

    def summary_line(self) -> str:
        parts = []
        if self.blocked_tool_calls:
            parts.append(f"{self.blocked_tool_calls} dup calls blocked")
        if self.tokens_saved_compression:
            parts.append(f"{self.tokens_saved_compression} tokens compressed")
        if self.loop_breaks:
            parts.append(f"{self.loop_breaks} loop breaks")
        if not parts:
            return "No savings recorded (guards active but nothing triggered)"
        cost = self.total_cost_saved_usd
        return f"RunCore saved: {', '.join(parts)} → ~${cost:.5f} saved"


# ---------------------------------------------------------------------------
# Guard engine
# ---------------------------------------------------------------------------

_AVG_TOKENS_PER_TOOL_CALL = 150  # conservative estimate per blocked call
_TOKEN_COST_USD = 3e-6


class GuardEngine:
    """Stateful guard engine attached to a single Capture session.

    All methods are called by :class:`~runcore.sdk.capture.Capture` at the
    appropriate interception points.
    """

    def __init__(self, config: GuardConfig) -> None:
        self.config = config
        self.savings = SavingsReport()

        # Dedup state
        self._session_seen: set[str] = set()   # entire session
        self._turn_seen: set[str] = set()       # current LLM turn
        self._tool_call_count: int = 0

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    def new_turn(self) -> None:
        """Call this before each LLM invocation to reset turn-scoped dedup."""
        self._turn_seen.clear()

    # ------------------------------------------------------------------
    # Dedup guard
    # ------------------------------------------------------------------

    def check_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        """Raise DuplicateToolCallError if this call is a duplicate.

        Should be called *before* executing the tool.
        """
        if not self.config.dedup_enabled:
            return

        sig = f"{name}:{json.dumps(arguments, sort_keys=True)}"
        seen = self._turn_seen if self.config.dedup_scope == "turn" else self._session_seen

        if sig in seen:
            # Estimate savings from this blocked call
            tokens_saved = _AVG_TOKENS_PER_TOOL_CALL
            self.savings.blocked_tool_calls += 1
            self.savings.blocked_tool_calls_tokens += tokens_saved
            self.savings.blocked_tool_calls_cost_usd += tokens_saved * _TOKEN_COST_USD
            raise DuplicateToolCallError(name, arguments)

        seen.add(sig)
        self._session_seen.add(sig)
        self._tool_call_count += 1

    # ------------------------------------------------------------------
    # Loop break guard
    # ------------------------------------------------------------------

    def check_loop_risk(self, loop_risk_score: float) -> None:
        """Raise LoopBreakError if loop risk exceeds threshold.

        Should be called after computing the current Loop Risk Score,
        typically after each LLM turn.
        """
        if not self.config.loop_break_enabled:
            return
        if self._tool_call_count < self.config.loop_break_min_calls:
            return
        if loop_risk_score > self.config.loop_break_threshold:
            self.savings.loop_breaks += 1
            raise LoopBreakError(loop_risk_score, self.config.loop_break_threshold)

    # ------------------------------------------------------------------
    # Context compression guard
    # ------------------------------------------------------------------

    def maybe_compress(
        self,
        messages: list[dict],
        estimated_tokens: int,
        task: str = "",
    ) -> list[dict]:
        """Return (possibly compressed) messages.

        If context_compression_enabled and estimated_tokens exceeds the
        threshold, runs ContextCompiler and records the savings.
        Returns the original messages unchanged if guard is off or below
        threshold.
        """
        if not self.config.context_compression_enabled:
            return messages
        if estimated_tokens < self.config.context_compression_token_threshold:
            return messages

        try:
            from runcore.context.compiler import ContextCompiler
            compiler = ContextCompiler(
                dedup_threshold=self.config.context_compression_dedup_threshold,
            )
            result = compiler.compile(messages, task=task)
            tokens_saved = result.get("token_reduction", 0)
            if tokens_saved > 0:
                self.savings.compression_runs += 1
                self.savings.tokens_saved_compression += tokens_saved
                self.savings.cost_saved_compression_usd += tokens_saved * _TOKEN_COST_USD
            return result["compiled_messages"]
        except Exception:
            # Never break the agent — compression is best-effort
            return messages
