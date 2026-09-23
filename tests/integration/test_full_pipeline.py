"""Integration tests — full pipeline."""
import json
import tempfile
import os
from pathlib import Path

import pytest

from runcore.agents.simulated import SimulatedAgentFactory
from runcore.benchmark.runner import BenchmarkRunner
from runcore.benchmark.metrics import calculate_metrics
from runcore.benchmark.comparison import BenchmarkComparison
from runcore.core.models import OptimizationConfig
from runcore.reports.generator import ReportGenerator
from runcore.trace.storage import save_trace, load_trace


def test_support_agent_full_pipeline():
    """Run support agent → trace → optimize → benchmark → report."""
    factory = SimulatedAgentFactory()
    agent = factory.create("support")
    runner = BenchmarkRunner()
    config = OptimizationConfig(
        max_tools=3,
        min_quality_threshold=0.75,
        cost_reduction_target=0.20,
    )

    tasks = ["Refund invoice #1001 for john@example.com"]
    baseline = runner.run_baseline(agent, tasks, runs_per_task=5)
    optimized = runner.run_optimized(agent, tasks, config, runs_per_task=5)

    assert len(baseline) == 5
    assert len(optimized) == 5
    assert all(t.success for t in baseline)

    bm = calculate_metrics(baseline)
    om = calculate_metrics(optimized)
    assert bm.avg_cost > 0
    assert om.avg_cost < bm.avg_cost

    cmp = BenchmarkComparison()
    result = cmp.compare(bm, om, config, baseline, optimized)
    assert result.cost_savings_pct > 0
    assert result.result in ("PASS", "FAIL")


def test_benchmark_produces_report():
    factory = SimulatedAgentFactory()
    agent = factory.create("support")
    runner = BenchmarkRunner()
    config = OptimizationConfig()

    baseline = runner.run_baseline(agent, ["test task"], runs_per_task=3)
    optimized = runner.run_optimized(agent, ["test task"], config, runs_per_task=3)
    bm = calculate_metrics(baseline)
    om = calculate_metrics(optimized)

    cmp = BenchmarkComparison()
    result = cmp.compare(bm, om, config, baseline, optimized)

    gen = ReportGenerator()
    text_report = gen.generate_text(result)
    json_report = gen.generate_json(result)
    html_report = gen.generate_html(result, bm, om)

    assert "RunCore Benchmark Report" in text_report
    assert "PASS" in text_report or "FAIL" in text_report

    parsed = json.loads(json_report)
    assert "cost_savings_pct" in parsed
    assert "result" in parsed

    assert "<html" in html_report.lower() or "<pre>" in html_report


def test_trace_save_and_load():
    factory = SimulatedAgentFactory()
    agent = factory.create("coding")
    trace = agent.run("fix bug in main.py")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        save_trace(trace, path)
        loaded = load_trace(path)
        assert loaded.run_id == trace.run_id
        assert loaded.agent_name == "coding"
        assert len(loaded.tool_calls) == len(trace.tool_calls)
        assert loaded.total_cost == pytest.approx(trace.total_cost, rel=0.001)
    finally:
        os.unlink(path)


def test_all_agent_types_produce_traces():
    factory = SimulatedAgentFactory()
    for agent_type in ["support", "research", "coding"]:
        agent = factory.create(agent_type)
        trace = agent.run(f"test task for {agent_type}")
        assert trace.success is True
        assert len(trace.llm_calls) >= 2
        assert len(trace.tool_calls) >= 2
        assert trace.total_cost > 0
        assert trace.total_tokens > 0


def test_cli_benchmark_command():
    """CLI benchmark command should run without error using the support fixture."""
    from typer.testing import CliRunner
    from runcore.cli.main import app

    runner = CliRunner()
    fixture_path = str(Path("tests/fixtures/support.json").resolve())
    result = runner.invoke(app, ["benchmark", fixture_path, "--runs", "2"])

    # The command should exit 0 (success) even if optimizations fail quality checks
    assert result.exit_code == 0, f"CLI failed with output:\n{result.output}"
    # Output should mention benchmark results
    assert "Savings" in result.output or "Result" in result.output or "RunCore" in result.output


def test_benchmark_fixture_format():
    """Test with the standard fixture file."""
    fixture_path = Path("tests/fixtures/support.json")
    if not fixture_path.exists():
        pytest.skip("Fixture file not found")

    with open(fixture_path) as f:
        fix = json.load(f)

    assert "agent" in fix
    assert "tasks" in fix
    assert len(fix["tasks"]) > 0

    factory = SimulatedAgentFactory()
    agent = factory.create(fix["agent"])
    runner = BenchmarkRunner()
    config = OptimizationConfig(**{k: v for k, v in fix.get("config", {}).items() if k in OptimizationConfig.model_fields})

    baseline = runner.run_baseline(agent, fix["tasks"][:1], runs_per_task=2)
    optimized = runner.run_optimized(agent, fix["tasks"][:1], config, runs_per_task=2)
    bm = calculate_metrics(baseline)
    om = calculate_metrics(optimized)

    cmp = BenchmarkComparison()
    result = cmp.compare(bm, om, config, baseline, optimized)
    assert result.runs > 0
