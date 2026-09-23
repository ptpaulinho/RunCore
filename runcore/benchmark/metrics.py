"""Benchmark metrics calculation."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import List

from runcore.core.models import AgentTrace


@dataclass
class BenchmarkMetrics:
    runs: int = 0
    avg_cost: float = 0.0
    avg_tokens: int = 0
    avg_latency_ms: float = 0.0
    avg_tool_calls: float = 0.0
    success_rate: float = 0.0
    avg_quality_score: float = 0.0
    cost_per_successful_task: float = 0.0
    p95_cost: float = 0.0
    p95_latency: float = 0.0


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


def calculate_metrics(traces: list[AgentTrace]) -> BenchmarkMetrics:
    if not traces:
        return BenchmarkMetrics()

    costs = [t.total_cost for t in traces]
    tokens = [t.total_tokens for t in traces]
    latencies = [t.latency_ms for t in traces]
    tool_counts = [len(t.tool_calls) for t in traces]
    successes = [t.success for t in traces]
    qualities = [t.quality_score for t in traces if t.quality_score is not None]

    success_rate = sum(successes) / len(successes)
    avg_cost = statistics.mean(costs)
    successful_traces = [t for t in traces if t.success]
    cost_per_success = (
        statistics.mean([t.total_cost for t in successful_traces])
        if successful_traces
        else float("inf")
    )

    return BenchmarkMetrics(
        runs=len(traces),
        avg_cost=avg_cost,
        avg_tokens=int(statistics.mean(tokens)),
        avg_latency_ms=statistics.mean(latencies),
        avg_tool_calls=statistics.mean(tool_counts),
        success_rate=success_rate,
        avg_quality_score=statistics.mean(qualities) if qualities else 0.0,
        cost_per_successful_task=cost_per_success,
        p95_cost=_percentile(costs, 95),
        p95_latency=_percentile(latencies, 95),
    )


def aggregate_metrics(metrics_list: list[BenchmarkMetrics]) -> BenchmarkMetrics:
    if not metrics_list:
        return BenchmarkMetrics()
    total_runs = sum(m.runs for m in metrics_list)
    weights = [m.runs / total_runs for m in metrics_list]

    def wavg(attr: str) -> float:
        return sum(getattr(m, attr) * w for m, w in zip(metrics_list, weights))

    return BenchmarkMetrics(
        runs=total_runs,
        avg_cost=wavg("avg_cost"),
        avg_tokens=int(wavg("avg_tokens")),
        avg_latency_ms=wavg("avg_latency_ms"),
        avg_tool_calls=wavg("avg_tool_calls"),
        success_rate=wavg("success_rate"),
        avg_quality_score=wavg("avg_quality_score"),
        cost_per_successful_task=wavg("cost_per_successful_task"),
        p95_cost=max(m.p95_cost for m in metrics_list),
        p95_latency=max(m.p95_latency for m in metrics_list),
    )
