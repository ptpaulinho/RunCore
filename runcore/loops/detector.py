"""Loop detection logic for AgentTrace instances."""

from __future__ import annotations

from collections import Counter
from typing import Any

from runcore.core.models import AgentTrace, ToolCall
from runcore.loops.similarity import calls_are_identical, compute_call_signature


class LoopDetector:
    """Detects various loop patterns inside an :class:`~runcore.core.models.AgentTrace`."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_same_tool_calls(self, trace: AgentTrace) -> list[dict[str, Any]]:
        """Return groups of tool calls that are exact duplicates of each other.

        Each entry in the returned list is a dict with keys:

        * ``signature``  – the shared call signature hash
        * ``name``       – tool name
        * ``count``      – number of times the call was made
        * ``indices``    – positions in ``trace.tool_calls`` (0-based)
        """
        groups: dict[str, dict[str, Any]] = {}
        for idx, call in enumerate(trace.tool_calls):
            sig = compute_call_signature(call)
            if sig not in groups:
                groups[sig] = {
                    "signature": sig,
                    "name": call.name,
                    "count": 0,
                    "indices": [],
                }
            groups[sig]["count"] += 1
            groups[sig]["indices"].append(idx)

        return [g for g in groups.values() if g["count"] > 1]

    def detect_repeated_errors(self, trace: AgentTrace) -> list[dict[str, Any]]:
        """Return groups of failed tool calls that share the same name.

        Each entry contains:

        * ``name``    – tool name
        * ``count``   – number of failures
        * ``indices`` – positions in ``trace.tool_calls``
        """
        error_groups: dict[str, dict[str, Any]] = {}
        for idx, call in enumerate(trace.tool_calls):
            if not call.success:
                name = call.name
                if name not in error_groups:
                    error_groups[name] = {"name": name, "count": 0, "indices": []}
                error_groups[name]["count"] += 1
                error_groups[name]["indices"].append(idx)

        return [g for g in error_groups.values() if g["count"] > 1]

    def detect_no_progress_cycles(
        self, trace: AgentTrace, window: int = 5
    ) -> list[dict[str, Any]]:
        """Detect sliding windows of *window* consecutive calls that are all identical."""
        calls = trace.tool_calls
        if len(calls) < window:
            return []

        cycles: list[dict[str, Any]] = []
        i = 0
        while i <= len(calls) - window:
            base = calls[i]
            base_sig = compute_call_signature(base)
            if all(calls_are_identical(base, calls[i + j]) for j in range(1, window)):
                cycles.append({
                    "start_index": i,
                    "end_index": i + window - 1,
                    "name": base.name,
                    "signature": base_sig,
                })
                i += window
            else:
                i += 1
        return cycles

    def detect_cross_turn_loops(
        self, trace: AgentTrace, min_gap: int = 2
    ) -> list[dict[str, Any]]:
        """Detect the same tool+args called multiple times with at least *min_gap* other
        calls in between — i.e. the agent forgot it already ran the call.

        Returns list of dicts:
        * ``name``       – tool name
        * ``signature``  – call signature
        * ``count``      – total occurrences
        * ``indices``    – positions (may be non-consecutive)
        * ``max_gap``    – largest gap between any two occurrences
        """
        groups: dict[str, dict[str, Any]] = {}
        for idx, call in enumerate(trace.tool_calls):
            sig = compute_call_signature(call)
            if sig not in groups:
                groups[sig] = {"name": call.name, "signature": sig, "count": 0, "indices": []}
            groups[sig]["count"] += 1
            groups[sig]["indices"].append(idx)

        results = []
        for g in groups.values():
            if g["count"] < 2:
                continue
            indices = g["indices"]
            gaps = [indices[i + 1] - indices[i] for i in range(len(indices) - 1)]
            max_gap = max(gaps)
            if max_gap >= min_gap:
                g["max_gap"] = max_gap
                results.append(g)
        return results

    def calculate_loop_risk_score(self, trace: AgentTrace) -> float:
        """Return a loop-risk score in [0, 1].

        Weighted combination of four signals:
        * duplicate call ratio     (weight 0.35)
        * error repetition ratio   (weight 0.25)
        * no-progress cycle cover  (weight 0.20)
        * cross-turn loop ratio    (weight 0.20)
        """
        calls = trace.tool_calls
        n = len(calls)
        if n == 0:
            return 0.0

        dup_groups = self.detect_same_tool_calls(trace)
        dup_ratio = min(sum(g["count"] - 1 for g in dup_groups) / n, 1.0)

        error_groups = self.detect_repeated_errors(trace)
        error_ratio = min(sum(g["count"] for g in error_groups) / n, 1.0)

        cycle_groups = self.detect_no_progress_cycles(trace)
        cycle_ratio = min(
            sum(g["end_index"] - g["start_index"] + 1 for g in cycle_groups) / n, 1.0
        )

        cross_groups = self.detect_cross_turn_loops(trace, min_gap=2)
        cross_ratio = min(sum(g["count"] - 1 for g in cross_groups) / n, 1.0)

        score = 0.35 * dup_ratio + 0.25 * error_ratio + 0.20 * cycle_ratio + 0.20 * cross_ratio
        return round(min(score, 1.0), 4)

    def get_loop_report(self, trace: AgentTrace) -> dict[str, Any]:
        """Return a comprehensive loop-analysis report for *trace*.

        Keys:

        * ``run_id``              – trace run identifier
        * ``agent_name``          – agent name
        * ``total_tool_calls``    – total number of tool calls
        * ``risk_score``          – float in [0, 1]
        * ``same_tool_calls``     – output of :meth:`detect_same_tool_calls`
        * ``repeated_errors``     – output of :meth:`detect_repeated_errors`
        * ``no_progress_cycles``  – output of :meth:`detect_no_progress_cycles`
        """
        return {
            "run_id": trace.run_id,
            "agent_name": trace.agent_name,
            "total_tool_calls": len(trace.tool_calls),
            "risk_score": self.calculate_loop_risk_score(trace),
            "same_tool_calls": self.detect_same_tool_calls(trace),
            "repeated_errors": self.detect_repeated_errors(trace),
            "no_progress_cycles": self.detect_no_progress_cycles(trace),
            "cross_turn_loops": self.detect_cross_turn_loops(trace),
        }
