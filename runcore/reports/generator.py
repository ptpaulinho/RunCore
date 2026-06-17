"""Report generator — JSON, HTML, and text output."""
from __future__ import annotations

import json
import os
from pathlib import Path

from runcore.benchmark.metrics import BenchmarkMetrics
from runcore.core.models import BenchmarkResult

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class ReportGenerator:
    def generate_json(self, result: BenchmarkResult) -> str:
        data = result.model_dump(mode="json")
        return json.dumps(data, indent=2, default=str)

    def generate_html(self, result: BenchmarkResult, baseline_metrics: BenchmarkMetrics | None = None, optimized_metrics: BenchmarkMetrics | None = None) -> str:
        try:
            from jinja2 import Environment, FileSystemLoader
            env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=False)
            tmpl = env.get_template("report.html.j2")

            # Build synthetic metrics from the result if not provided
            if baseline_metrics is None:
                baseline_metrics = BenchmarkMetrics(
                    runs=result.runs,
                    avg_cost=result.baseline.total_cost,
                    avg_tokens=result.baseline.total_tokens,
                    avg_latency_ms=result.baseline.latency_ms,
                    avg_tool_calls=len(result.baseline.tool_calls),
                    success_rate=1.0 if result.baseline.success else 0.0,
                    avg_quality_score=result.baseline.quality_score or 0.0,
                    cost_per_successful_task=result.baseline.total_cost,
                )
            if optimized_metrics is None:
                optimized_metrics = BenchmarkMetrics(
                    runs=result.runs,
                    avg_cost=result.optimized.total_cost,
                    avg_tokens=result.optimized.total_tokens,
                    avg_latency_ms=result.optimized.latency_ms,
                    avg_tool_calls=len(result.optimized.tool_calls),
                    success_rate=1.0 if result.optimized.success else 0.0,
                    avg_quality_score=result.optimized.quality_score or 0.0,
                    cost_per_successful_task=result.optimized.total_cost,
                )

            return tmpl.render(
                br=result,
                baseline=baseline_metrics,
                optimized=optimized_metrics,
                result=result.result,
                result_class="pass" if result.result == "PASS" else "fail",
            )
        except ImportError:
            return f"<pre>{self.generate_text(result)}</pre>"

    def generate_text(self, result: BenchmarkResult) -> str:
        b = result.baseline
        o = result.optimized
        lines = [
            "┌─────────────────────────────────────┐",
            "│      RunCore Benchmark Report       │",
            "└─────────────────────────────────────┘",
            f"Runs: {result.runs}",
            "",
            "Baseline:",
            f"  Cost:       ${b.total_cost:.4f}",
            f"  Tokens:     {b.total_tokens}",
            f"  Tool calls: {len(b.tool_calls)}",
            f"  Latency:    {b.latency_ms:.0f}ms",
            f"  Success:    {'Yes' if b.success else 'No'}",
            f"  Quality:    {b.quality_score:.3f}" if b.quality_score else "  Quality:    n/a",
            "",
            "RunCore Optimized:",
            f"  Cost:       ${o.total_cost:.4f}",
            f"  Tokens:     {o.total_tokens}",
            f"  Tool calls: {len(o.tool_calls)}",
            f"  Latency:    {o.latency_ms:.0f}ms",
            f"  Success:    {'Yes' if o.success else 'No'}",
            f"  Quality:    {o.quality_score:.3f}" if o.quality_score else "  Quality:    n/a",
            "",
            f"Savings:     {result.cost_savings_pct:.1f}%",
            f"Latency:     {result.latency_change_pct:+.1f}%",
            f"Tool calls:  {result.tool_call_reduction_pct:.1f}% fewer",
            f"Tokens:      {result.token_reduction_pct:.1f}% fewer",
            "",
            f"Result: {result.result}",
        ]
        return "\n".join(lines)

    def save_report(self, result: BenchmarkResult, path: str, format: str = "json", baseline_metrics: BenchmarkMetrics | None = None, optimized_metrics: BenchmarkMetrics | None = None) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if format == "json":
            content = self.generate_json(result)
        elif format == "html":
            content = self.generate_html(result, baseline_metrics, optimized_metrics)
        else:
            content = self.generate_text(result)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path
