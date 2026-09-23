"""Benchmark comparison — baseline vs optimized."""
from __future__ import annotations

from runcore.benchmark.metrics import BenchmarkMetrics
from runcore.core.models import AgentTrace, BenchmarkResult, OptimizationConfig
from runcore.core.enums import OptimizationResult


def _run_replacement_analysis(baseline_traces: list[AgentTrace]) -> list[dict]:
    """Run ReplacementDetector across all baseline traces and merge findings."""
    try:
        from runcore.replacement.detector import ReplacementDetector
        from runcore.core.models import AgentTrace as _AT

        # Merge all tool calls into one synthetic trace for analysis
        all_tool_calls = [tc for tr in baseline_traces for tc in tr.tool_calls]
        if not all_tool_calls:
            return []

        synthetic = AgentTrace(
            agent_name="merged", task="analysis",
            success=True, tool_calls=all_tool_calls,
        )
        detector = ReplacementDetector()
        findings = detector.analyze_trace(synthetic)
        # Only return findings with meaningful replaceability
        return [f for f in findings if f.get("replaceability_score", 0) >= 0.5]
    except Exception:
        return []


def _pct_change(baseline: float, optimized: float) -> float:
    if baseline == 0:
        return 0.0
    return (baseline - optimized) / baseline * 100.0


class BenchmarkComparison:
    def compare(
        self,
        baseline_metrics: BenchmarkMetrics,
        optimized_metrics: BenchmarkMetrics,
        config: OptimizationConfig,
        baseline_traces: list[AgentTrace] | None = None,
        optimized_traces: list[AgentTrace] | None = None,
    ) -> BenchmarkResult:
        cost_savings = _pct_change(baseline_metrics.avg_cost, optimized_metrics.avg_cost)
        token_reduction = _pct_change(baseline_metrics.avg_tokens, optimized_metrics.avg_tokens)
        tool_reduction = _pct_change(baseline_metrics.avg_tool_calls, optimized_metrics.avg_tool_calls)
        latency_change = _pct_change(baseline_metrics.avg_latency_ms, optimized_metrics.avg_latency_ms)
        success_delta = optimized_metrics.success_rate - baseline_metrics.success_rate
        quality_delta = optimized_metrics.avg_quality_score - baseline_metrics.avg_quality_score

        passes = self.validate_optimization_metrics(
            baseline_metrics, optimized_metrics, config, cost_savings, quality_delta
        )
        result_str = OptimizationResult.PASS.value if passes else OptimizationResult.FAIL.value

        # Use first traces as representative samples if provided
        b_trace = (baseline_traces[0] if baseline_traces else AgentTrace(
            agent_name="baseline", task="benchmark", success=baseline_metrics.success_rate >= 0.5,
            total_cost=baseline_metrics.avg_cost, total_tokens=baseline_metrics.avg_tokens,
            latency_ms=baseline_metrics.avg_latency_ms,
        ))
        o_trace = (optimized_traces[0] if optimized_traces else AgentTrace(
            agent_name="optimized", task="benchmark", success=optimized_metrics.success_rate >= 0.5,
            total_cost=optimized_metrics.avg_cost, total_tokens=optimized_metrics.avg_tokens,
            latency_ms=optimized_metrics.avg_latency_ms,
        ))

        replacement_findings = _run_replacement_analysis(baseline_traces or [])

        return BenchmarkResult(
            baseline=b_trace,
            optimized=o_trace,
            runs=baseline_metrics.runs,
            cost_savings_pct=round(cost_savings, 2),
            token_reduction_pct=round(token_reduction, 2),
            tool_call_reduction_pct=round(tool_reduction, 2),
            latency_change_pct=round(-latency_change, 2),
            success_rate_delta=round(success_delta, 4),
            quality_delta=round(quality_delta, 4),
            result=result_str,
            replacement_findings=replacement_findings,
        )

    def validate_optimization(self, result: BenchmarkResult) -> bool:
        return result.result == OptimizationResult.PASS.value

    def validate_optimization_metrics(
        self,
        baseline: BenchmarkMetrics,
        optimized: BenchmarkMetrics,
        config: OptimizationConfig,
        cost_savings: float,
        quality_delta: float,
    ) -> bool:
        cost_ok = cost_savings >= (config.cost_reduction_target * 100 * 0.5)  # allow 50% of target
        quality_ok = (optimized.avg_quality_score >= config.min_quality_threshold) or (quality_delta >= -0.05)
        success_ok = (baseline.success_rate - optimized.success_rate) <= 0.02
        return cost_ok and quality_ok and success_ok

    def format_summary(self, result: BenchmarkResult) -> str:
        b = result.baseline
        o = result.optimized
        lines = [
            "┌─────────────────────────────────────┐",
            "│      RunCore Benchmark Report       │",
            "└─────────────────────────────────────┘",
            f"Runs: {result.runs}",
            "",
            "Baseline:",
            f"  Cost:    ${b.total_cost:.4f}",
            f"  Tokens:  {b.total_tokens}",
            f"  Success: {b.success}",
            "",
            "RunCore Optimized:",
            f"  Cost:    ${o.total_cost:.4f}",
            f"  Tokens:  {o.total_tokens}",
            f"  Success: {o.success}",
            "",
            f"Savings:    {result.cost_savings_pct:.1f}%",
            f"Latency:    {result.latency_change_pct:+.1f}%",
            f"Tool calls: {result.tool_call_reduction_pct:.1f}% fewer",
            f"Tokens:     {result.token_reduction_pct:.1f}% fewer",
            f"Quality:    {result.quality_delta:+.3f}",
            "",
            f"Result: {result.result}",
        ]
        if result.replacement_findings:
            lines += [
                "",
                "Replacement Opportunities:",
            ]
            for f in result.replacement_findings[:5]:
                score = f.get("replaceability_score", 0)
                ptype = f.get("pattern_type") or "unknown"
                lines.append(f"  {f['tool']:20s}  score={score:.2f}  type={ptype}")
        return "\n".join(lines)
