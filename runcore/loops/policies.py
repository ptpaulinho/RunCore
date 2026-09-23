"""Loop policy engine: applies loop-mitigation policies to AgentTrace instances."""

from __future__ import annotations

import copy
from typing import Any

from runcore.core.enums import LoopPolicy
from runcore.core.models import AgentTrace, ToolCall
from runcore.loops.detector import LoopDetector
from runcore.loops.similarity import calls_are_identical, compute_call_signature


class LoopPolicyEngine:
    """Apply :class:`~runcore.core.enums.LoopPolicy` strategies to an
    :class:`~runcore.core.models.AgentTrace`."""

    def __init__(self) -> None:
        self._detector = LoopDetector()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, trace: AgentTrace, policy: LoopPolicy) -> AgentTrace:
        """Return a (possibly modified) copy of *trace* after applying *policy*.

        * ``LoopPolicy.ABORT``          – marks the trace as failed and clears
          metadata loop flags; the caller should stop processing.
        * ``LoopPolicy.WARN``           – annotates ``trace.metadata`` with a
          ``loop_warnings`` key but leaves calls intact.
        * ``LoopPolicy.SKIP_DUPLICATE`` – removes duplicate tool calls via
          :meth:`deduplicate_calls`.
        * ``LoopPolicy.CONTINUE``       – returns the trace unchanged.
        """
        # Work on a deep copy so the original is not mutated
        modified = trace.model_copy(deep=True)

        if policy == LoopPolicy.ABORT:
            modified.success = False
            modified.metadata["loop_policy_applied"] = LoopPolicy.ABORT.value
            modified.metadata["loop_aborted"] = True

        elif policy == LoopPolicy.WARN:
            report = self._detector.get_loop_report(modified)
            warnings: list[str] = []
            if report["same_tool_calls"]:
                warnings.append(
                    f"{len(report['same_tool_calls'])} duplicate call group(s) detected."
                )
            if report["repeated_errors"]:
                warnings.append(
                    f"{len(report['repeated_errors'])} repeated error group(s) detected."
                )
            if report["no_progress_cycles"]:
                warnings.append(
                    f"{len(report['no_progress_cycles'])} no-progress cycle(s) detected."
                )
            modified.metadata["loop_policy_applied"] = LoopPolicy.WARN.value
            modified.metadata["loop_warnings"] = warnings

        elif policy == LoopPolicy.SKIP_DUPLICATE:
            modified.tool_calls = self.deduplicate_calls(modified.tool_calls)
            modified.metadata["loop_policy_applied"] = LoopPolicy.SKIP_DUPLICATE.value

        elif policy == LoopPolicy.CONTINUE:
            modified.metadata["loop_policy_applied"] = LoopPolicy.CONTINUE.value

        return modified

    def should_abort(self, trace: AgentTrace, max_risk: float = 0.8) -> bool:
        """Return ``True`` when the loop risk score exceeds *max_risk*."""
        score = self._detector.calculate_loop_risk_score(trace)
        return score >= max_risk

    def suggest_policy(self, trace: AgentTrace) -> LoopPolicy:
        """Recommend an appropriate :class:`~runcore.core.enums.LoopPolicy`.

        Heuristic:

        * risk >= 0.8  → ABORT
        * risk >= 0.5  → SKIP_DUPLICATE (there are duplicates worth removing)
        * risk >= 0.2  → WARN
        * otherwise    → CONTINUE
        """
        score = self._detector.calculate_loop_risk_score(trace)

        if score >= 0.8:
            return LoopPolicy.ABORT
        if score >= 0.5:
            return LoopPolicy.SKIP_DUPLICATE
        if score >= 0.2:
            return LoopPolicy.WARN
        return LoopPolicy.CONTINUE

    def deduplicate_calls(self, tool_calls: list[ToolCall]) -> list[ToolCall]:
        """Return a new list with consecutive and global duplicate calls removed.

        The first occurrence of each unique call signature is kept; subsequent
        identical calls are dropped.  Order of first occurrences is preserved.
        """
        seen: set[str] = set()
        unique: list[ToolCall] = []
        for call in tool_calls:
            sig = compute_call_signature(call)
            if sig not in seen:
                seen.add(sig)
                unique.append(call)
        return unique
