"""Task evaluation — quality and success scoring."""
from __future__ import annotations

from runcore.core.models import AgentTrace


class TaskEvaluator:
    def evaluate_quality(self, trace: AgentTrace, expected_output: str | None = None) -> float:
        if trace.quality_score is not None:
            base = trace.quality_score
        else:
            base = 0.75

        # Penalise loops: repeated identical tool calls lower quality
        tool_names = [tc.name for tc in trace.tool_calls]
        unique_ratio = len(set(tool_names)) / max(len(tool_names), 1)
        loop_penalty = max(0.0, (1 - unique_ratio) * 0.15)

        # Penalise failures
        failed_calls = sum(1 for tc in trace.tool_calls if not tc.success)
        failure_penalty = min(0.2, failed_calls * 0.05)

        return max(0.0, min(1.0, base - loop_penalty - failure_penalty))

    def evaluate_success(self, trace: AgentTrace) -> bool:
        return trace.success and all(tc.success for tc in trace.tool_calls[-1:])

    def score_trace(self, trace: AgentTrace) -> dict:
        quality = self.evaluate_quality(trace)
        success = self.evaluate_success(trace)
        tool_efficiency = len(set(tc.name for tc in trace.tool_calls)) / max(len(trace.tool_calls), 1)
        return {
            "quality": quality,
            "success": success,
            "efficiency": tool_efficiency,
            "cost": trace.total_cost,
            "tokens": trace.total_tokens,
        }
