"""Unit tests for benchmark engine."""
import pytest

from runcore.benchmark.metrics import calculate_metrics, BenchmarkMetrics
from runcore.benchmark.comparison import BenchmarkComparison
from runcore.benchmark.runner import BenchmarkRunner
from runcore.benchmark.evaluator import TaskEvaluator
from runcore.agents.support import SupportAgent
from runcore.core.models import OptimizationConfig, AgentTrace


def _make_metrics(avg_cost: float, success_rate: float = 1.0, quality: float = 0.85, avg_tools: float = 4.0) -> BenchmarkMetrics:
    return BenchmarkMetrics(
        runs=10,
        avg_cost=avg_cost,
        avg_tokens=1000,
        avg_latency_ms=500.0,
        avg_tool_calls=avg_tools,
        success_rate=success_rate,
        avg_quality_score=quality,
        cost_per_successful_task=avg_cost / max(success_rate, 0.001),
        p95_cost=avg_cost * 1.5,
        p95_latency=750.0,
    )


def test_baseline_vs_optimized_comparison():
    baseline = _make_metrics(avg_cost=0.20)
    optimized = _make_metrics(avg_cost=0.12)
    config = OptimizationConfig(cost_reduction_target=0.25, min_quality_threshold=0.80)
    cmp = BenchmarkComparison()
    result = cmp.compare(baseline, optimized, config)
    assert result.cost_savings_pct > 0
    assert result.result in ("PASS", "FAIL")


def test_savings_calculated_correctly():
    baseline = _make_metrics(avg_cost=0.20)
    optimized = _make_metrics(avg_cost=0.12)
    config = OptimizationConfig()
    cmp = BenchmarkComparison()
    result = cmp.compare(baseline, optimized, config)
    expected_savings = (0.20 - 0.12) / 0.20 * 100
    assert result.cost_savings_pct == pytest.approx(expected_savings, rel=0.01)


def test_reject_optimization_if_quality_fails():
    baseline = _make_metrics(avg_cost=0.20, quality=0.90)
    # Optimized has good cost savings but success rate drops > 2%
    optimized = _make_metrics(avg_cost=0.10, quality=0.60, success_rate=0.80)
    config = OptimizationConfig(min_quality_threshold=0.80, cost_reduction_target=0.10)
    cmp = BenchmarkComparison()
    result = cmp.compare(baseline, optimized, config)
    assert result.result == "FAIL"


def test_metrics_from_traces():
    agent = SupportAgent()
    traces = [agent.run("refund invoice #1001") for _ in range(5)]
    metrics = calculate_metrics(traces)
    assert metrics.runs == 5
    assert metrics.avg_cost > 0
    assert metrics.success_rate > 0
    assert 0 <= metrics.avg_quality_score <= 1


def test_benchmark_runner_produces_results():
    agent = SupportAgent()
    runner = BenchmarkRunner()
    config = OptimizationConfig()
    baseline = runner.run_baseline(agent, ["refund invoice #1001"], runs_per_task=3)
    optimized = runner.run_optimized(agent, ["refund invoice #1001"], config, runs_per_task=3)
    assert len(baseline) == 3
    assert len(optimized) == 3
    # Optimized should have lower average cost
    avg_baseline = sum(t.total_cost for t in baseline) / len(baseline)
    avg_optimized = sum(t.total_cost for t in optimized) / len(optimized)
    assert avg_optimized < avg_baseline
