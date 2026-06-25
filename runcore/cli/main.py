"""RunCore CLI — The efficiency standard for AI agents."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

app = typer.Typer(
    name="runcore",
    help="RunCore — The efficiency standard for AI agents. Measure, certify, and prove agent efficiency.",
    add_completion=False,
)
console = Console()


def _config_dir() -> Path:
    return Path(".runcore")


def _default_config() -> dict:
    return {
        "max_tools": 10,
        "min_quality_threshold": 0.80,
        "cost_reduction_target": 0.30,
        "enable_context_compression": True,
        "enable_loop_detection": True,
        "enable_tool_ranking": True,
    }


@app.command()
def init():
    """Initialize RunCore in the current directory."""
    cfg_dir = _config_dir()
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "traces").mkdir(exist_ok=True)
    (cfg_dir / "reports").mkdir(exist_ok=True)

    cfg_path = cfg_dir / "config.json"
    with open(cfg_path, "w") as f:
        json.dump(_default_config(), f, indent=2)

    typer.echo("RunCore initialized. Config saved to .runcore/config.json")
    console.print(Panel.fit(
        "[bold green]RunCore initialized[/bold green]\n"
        f"Config saved to [cyan]{cfg_path}[/cyan]",
        title="[bold purple]RunCore[/bold purple]",
    ))


@app.command()
def profile(
    agent: str = typer.Option(
        "support",
        "--agent",
        "-a",
        help="Agent type: support, research, coding",
    ),
    task: str = typer.Option(
        "Refund invoice #1001 for customer@example.com",
        "--task",
        "-t",
        help="Task description to run the agent on",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path for trace JSON (default: .runcore/traces/latest.json)",
    ),
):
    """Run an agent on a task and capture its execution trace."""
    from runcore.agents.simulated import SimulatedAgentFactory
    from runcore.trace.storage import save_trace

    valid_agents = ("support", "research", "coding")
    if agent not in valid_agents:
        console.print(f"[red]Unknown agent '{agent}'. Choose from: {valid_agents}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold]Profiling[/bold] [cyan]{agent}[/cyan] agent on task: [italic]{task[:80]}[/italic]")

    factory = SimulatedAgentFactory()
    ag = factory.create(agent)
    trace = ag.run(task)

    out_path = output or str(_config_dir() / "traces" / "latest.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    save_trace(trace, out_path)

    table = Table(title="Trace Summary", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Agent", trace.agent_name)
    table.add_row("Task", trace.task[:60])
    table.add_row("LLM calls", str(len(trace.llm_calls)))
    table.add_row("Tool calls", str(len(trace.tool_calls)))
    table.add_row("Total tokens", str(trace.total_tokens))
    table.add_row("Total cost", f"${trace.total_cost:.4f}")
    table.add_row("Latency", f"{trace.latency_ms:.0f}ms")
    table.add_row("Success", "[green]Yes[/green]" if trace.success else "[red]No[/red]")
    table.add_row("Quality", f"{trace.quality_score:.3f}" if trace.quality_score else "n/a")
    console.print(table)
    console.print(f"[dim]Trace saved → {out_path}[/dim]")


@app.command()
def compile(
    trace_path: str = typer.Option(
        ".runcore/traces/latest.json",
        "--trace",
        "-t",
        help="Path to trace JSON to compile",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path for the optimized trace JSON",
    ),
):
    """Load a trace, apply context compilation, and save the optimized version."""
    from runcore.trace.storage import load_trace, save_trace
    from runcore.context.compiler import ContextCompiler

    try:
        trace = load_trace(trace_path)
    except FileNotFoundError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)

    compiler = ContextCompiler()

    # Build a synthetic message list from the trace to feed into the compiler
    messages: list[dict] = [
        {"role": "system", "content": f"Agent: {trace.agent_name}"},
        {"role": "user", "content": trace.task},
    ]
    for i, llm_call in enumerate(trace.llm_calls):
        # Approximate the completion content by repeating a word to hit token count
        approx_words = max(1, llm_call.completion_tokens // 5)
        messages.append({"role": "assistant", "content": ("response " * approx_words).strip()})
    for tool_call in trace.tool_calls:
        messages.append({"role": "user", "content": f"tool:{tool_call.name} result:{str(tool_call.result)[:200]}"})

    result = compiler.compile(messages, task=trace.task)

    original_tokens: int = result.get("original_tokens", 0)
    final_tokens: int = result.get("final_tokens", 0)
    token_reduction_count: int = result.get("token_reduction", 0)
    blocks_removed: int = result.get("blocks_removed", 0)
    cache_score: float = result.get("cache_score", 0.0)

    pct = (token_reduction_count / original_tokens * 100) if original_tokens > 0 else 0.0

    out_path = output or trace_path.replace(".json", "_optimized.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    save_trace(trace, out_path)

    console.print(Panel.fit(
        f"[bold]Context Compilation Complete[/bold]\n"
        f"Original tokens:  [yellow]{original_tokens}[/yellow]\n"
        f"Final tokens:     [green]{final_tokens}[/green]\n"
        f"Token reduction:  [green]{token_reduction_count} ({pct:.1f}%)[/green]\n"
        f"Blocks removed:   [yellow]{blocks_removed}[/yellow]\n"
        f"Cache score:      [cyan]{cache_score:.3f}[/cyan]\n"
        f"Saved → [cyan]{out_path}[/cyan]",
        title="[bold purple]RunCore Compile[/bold purple]",
    ))


@app.command()
def benchmark(
    fixture: str = typer.Argument(
        "tests/fixtures/support.json",
        help='Path to benchmark fixture JSON: {"agent": "support", "tasks": [...], "config": {...}}',
    ),
    runs: int = typer.Option(
        10,
        "--runs",
        "-r",
        help="Number of runs per task for both baseline and optimized",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Base output path for reports (without extension)",
    ),
):
    """Load a fixture, run baseline + optimized benchmark, and save the report."""
    from runcore.agents.simulated import SimulatedAgentFactory
    from runcore.benchmark.runner import BenchmarkRunner
    from runcore.benchmark.metrics import calculate_metrics
    from runcore.benchmark.comparison import BenchmarkComparison
    from runcore.core.models import OptimizationConfig
    from runcore.reports.generator import ReportGenerator

    fixture_path = Path(fixture)
    if not fixture_path.exists():
        console.print(f"[red]Fixture not found: {fixture}[/red]")
        raise typer.Exit(code=1)

    with open(fixture_path) as f:
        fix = json.load(f)

    agent_type: str = fix.get("agent", "support")
    tasks: list[str] = fix.get("tasks", ["default task"])
    cfg_data: dict = fix.get("config", {})

    valid_fields = set(OptimizationConfig.model_fields.keys())
    config = OptimizationConfig(**{k: v for k, v in cfg_data.items() if k in valid_fields})

    factory = SimulatedAgentFactory()
    ag = factory.create(agent_type)
    runner = BenchmarkRunner()

    console.print(f"[bold]Benchmark[/bold]: agent=[cyan]{agent_type}[/cyan], runs=[cyan]{runs}[/cyan]/task, tasks=[cyan]{len(tasks)}[/cyan]")
    console.print(f"[bold]Running baseline[/bold] ({runs} runs × {len(tasks)} tasks = {runs * len(tasks)} total)...")
    baseline_traces = runner.run_baseline(ag, tasks, runs_per_task=runs)

    console.print(f"[bold]Running optimized[/bold] (with real optimization profile)...")
    optimized_traces = runner.run_optimized(ag, tasks, config, runs_per_task=runs, baseline_traces=baseline_traces)

    baseline_metrics = calculate_metrics(baseline_traces)
    optimized_metrics = calculate_metrics(optimized_traces)

    cmp = BenchmarkComparison()
    result = cmp.compare(baseline_metrics, optimized_metrics, config, baseline_traces, optimized_traces)

    out_dir = _config_dir() / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    base_path = output or str(out_dir / "benchmark")

    gen = ReportGenerator()
    json_path = gen.save_report(
        result, base_path + ".json", format="json",
        baseline_metrics=baseline_metrics, optimized_metrics=optimized_metrics,
    )
    html_path = gen.save_report(
        result, base_path + ".html", format="html",
        baseline_metrics=baseline_metrics, optimized_metrics=optimized_metrics,
    )

    console.print("\n" + cmp.format_summary(result))
    console.print(f"\n[dim]JSON report → {json_path}[/dim]")
    console.print(f"[dim]HTML report → {html_path}[/dim]")


@app.command()
def report(
    trace_path: str = typer.Option(
        ".runcore/reports/benchmark.json",
        "--trace",
        "-t",
        help="Path to a benchmark result JSON (produced by runcore benchmark)",
    ),
    format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: json, html, text",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Save report to this file path (default: print to stdout)",
    ),
):
    """Generate a report from a saved benchmark result in JSON, HTML, or text format."""
    from runcore.core.models import BenchmarkResult
    from runcore.reports.generator import ReportGenerator

    valid_formats = ("json", "html", "text")
    if format not in valid_formats:
        console.print(f"[red]Unknown format '{format}'. Choose from: {valid_formats}[/red]")
        raise typer.Exit(code=1)

    try:
        with open(trace_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        console.print(f"[red]File not found: {trace_path}[/red]")
        raise typer.Exit(code=1)

    result = BenchmarkResult.model_validate(data)
    gen = ReportGenerator()

    if format == "json":
        content = gen.generate_json(result)
    elif format == "html":
        content = gen.generate_html(result)
    else:
        content = gen.generate_text(result)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        gen.save_report(result, output, format=format)
        console.print(f"Report saved → [cyan]{output}[/cyan]")
    else:
        console.print(content)


@app.command(name="run-real")
def run_real(
    task: str = typer.Argument("Refund invoice INV-1001 for john@example.com", help="Task for the real agent"),
    runs: int = typer.Option(1, "--runs", "-r", help="Number of runs"),
    compare: bool = typer.Option(False, "--compare", "-c", help="Also run simulated agent and compare costs"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Save JSON report path"),
):
    """Run a real Anthropic LLM agent and show actual API cost and token usage.

    Requires ANTHROPIC_API_KEY to be set in the environment.
    """
    import os
    from runcore.agents.real import RealSupportAgent
    from runcore.agents.support import SupportAgent
    from runcore.benchmark.metrics import calculate_metrics

    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]ANTHROPIC_API_KEY not set.[/red]\n"
                      "Export it first:  [bold]export ANTHROPIC_API_KEY=sk-ant-...[/bold]")
        raise typer.Exit(1)

    console.print(f"[bold]Running real LLM agent[/bold] × {runs} on: [italic]{task[:70]}[/italic]")

    real_agent = RealSupportAgent()
    real_traces = []
    for i in range(runs):
        console.print(f"  Run {i+1}/{runs}…", end="\r")
        real_traces.append(real_agent.run(task))
    console.print()

    real_m = calculate_metrics(real_traces)

    table = Table(title="Real LLM Agent Results", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Model", real_agent.model)
    table.add_row("Runs", str(runs))
    table.add_row("Avg cost", f"${real_m.avg_cost:.5f}")
    table.add_row("Avg tokens", str(real_m.avg_tokens))
    table.add_row("Avg tool calls", f"{real_m.avg_tool_calls:.1f}")
    table.add_row("Avg latency", f"{real_m.avg_latency_ms:.0f}ms")
    table.add_row("Success rate", f"{real_m.success_rate*100:.0f}%")
    table.add_row("Avg quality", f"{real_m.avg_quality_score:.3f}")
    console.print(table)

    if compare:
        console.print("[bold]Running simulated agent for comparison…[/bold]")
        sim_agent = SupportAgent()
        sim_traces = [sim_agent.run(task) for _ in range(runs)]
        sim_m = calculate_metrics(sim_traces)

        cmp_table = Table(title="Real vs Simulated", box=box.ROUNDED)
        cmp_table.add_column("Metric", style="cyan")
        cmp_table.add_column("Real LLM", style="yellow")
        cmp_table.add_column("Simulated", style="blue")
        cmp_table.add_row("Avg cost", f"${real_m.avg_cost:.5f}", f"${sim_m.avg_cost:.5f}")
        cmp_table.add_row("Avg tokens", str(real_m.avg_tokens), str(sim_m.avg_tokens))
        cmp_table.add_row("Avg tool calls", f"{real_m.avg_tool_calls:.1f}", f"{sim_m.avg_tool_calls:.1f}")
        cmp_table.add_row("Avg latency", f"{real_m.avg_latency_ms:.0f}ms", f"{sim_m.avg_latency_ms:.0f}ms")
        console.print(cmp_table)

    if output:
        import json as _json
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        report = {
            "agent": real_agent.name,
            "model": real_agent.model,
            "task": task,
            "runs": runs,
            "metrics": {
                "avg_cost": real_m.avg_cost,
                "avg_tokens": real_m.avg_tokens,
                "avg_tool_calls": real_m.avg_tool_calls,
                "avg_latency_ms": real_m.avg_latency_ms,
                "success_rate": real_m.success_rate,
            },
        }
        Path(output).write_text(_json.dumps(report, indent=2))
        console.print(f"Report saved → [cyan]{output}[/cyan]")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
):
    """Start the RunCore web dashboard at http://localhost:8000."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn not installed. Run: pip install uvicorn[/red]")
        raise typer.Exit(1)

    _config_dir().mkdir(exist_ok=True)
    (_config_dir() / "reports").mkdir(exist_ok=True)

    console.print(Panel.fit(
        f"[bold green]RunCore Dashboard[/bold green]\n"
        f"Open [cyan]http://{host}:{port}[/cyan] in your browser\n"
        f"[dim]Ctrl+C to stop[/dim]",
        title="[bold purple]RunCore[/bold purple]",
    ))
    uvicorn.run(
        "runcore.server.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )


@app.command(name="atir")
def atir_cmd(
    subcommand: str = typer.Argument(..., help="Subcommand: validate, convert, show"),
    file: str = typer.Argument(..., help="Path to .atir.json file"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path"),
    format: str = typer.Option("pretty", "--format", "-f", help="Output format: pretty, json, summary"),
):
    """Work with ATIR trace files (validate, convert, show).

    Examples::

        runcore atir validate trace.atir.json
        runcore atir show trace.atir.json
        runcore atir convert trace.atir.json --output out.atir.json
    """
    from runcore.atir.spec import ATIRTrace
    from runcore.atir.converter import from_dict

    valid_subcmds = ("validate", "show", "convert")
    if subcommand not in valid_subcmds:
        console.print(f"[red]Unknown subcommand '{subcommand}'. Choose from: {valid_subcmds}[/red]")
        raise typer.Exit(code=1)

    file_path = Path(file)
    if not file_path.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(code=1)

    try:
        data = json.loads(file_path.read_text())
        trace = from_dict(data)
    except Exception as exc:
        console.print(f"[red]Failed to parse ATIR file: {exc}[/red]")
        raise typer.Exit(code=1)

    if subcommand == "validate":
        console.print(Panel.fit(
            f"[bold green]ATIR trace is valid[/bold green]\n"
            f"Version:    [cyan]{trace.atir_version}[/cyan]\n"
            f"Agent:      [cyan]{trace.agent_name}[/cyan]\n"
            f"Spans:      [cyan]{len(trace.spans)}[/cyan]\n"
            f"Provider:   [cyan]{trace.provider}[/cyan]\n"
            f"Framework:  [cyan]{trace.framework}[/cyan]",
            title="[bold purple]ATIR Validate[/bold purple]",
        ))

    elif subcommand == "show":
        agg = trace.aggregates
        table = Table(title=f"ATIR Trace: {trace.trace_id[:16]}…", box=box.ROUNDED)
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Agent", trace.agent_name)
        table.add_row("Task", (trace.task or "")[:60])
        table.add_row("Provider", trace.provider)
        table.add_row("Framework", trace.framework)
        table.add_row("Success", "[green]Yes[/green]" if trace.success else "[red]No[/red]")
        table.add_row("Quality", f"{trace.quality_score:.3f}" if trace.quality_score else "n/a")
        table.add_row("Spans", str(len(trace.spans)))
        if agg:
            table.add_row("LLM calls", str(agg.llm_calls))
            table.add_row("Tool calls", str(agg.tool_calls))
            table.add_row("Duplicate tools", str(agg.duplicate_tool_calls))
            table.add_row("Total tokens", str(agg.total_tokens))
            table.add_row("Total cost", f"${agg.total_cost_usd:.6f}")
            table.add_row("CpST", f"${agg.cost_per_successful_task:.6f}")
        console.print(table)

    elif subcommand == "convert":
        out_path = output or file.replace(".json", ".converted.json")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(trace.to_dict(), indent=2))
        console.print(f"[green]Converted → {out_path}[/green]")


@app.command(name="import")
def import_trace(
    file: str = typer.Argument(..., help="Path to trace file (.atir.json, or raw JSON)"),
    source: str = typer.Option(
        "atir",
        "--source",
        "-s",
        help="Source format: atir, openai, anthropic",
    ),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Save imported trace to this path"),
    show: bool = typer.Option(True, "--show/--no-show", help="Print trace summary to terminal"),
):
    """Import an agent trace from any supported format into ATIR.

    Supported source formats:

    - atir      — already an ATIR v1 JSON file
    - openai    — raw OpenAI chat completion response JSON
    - anthropic — raw Anthropic messages response JSON

    Example::

        runcore import trace.json --source anthropic --output trace.atir.json
    """
    from runcore.atir.converter import from_dict, from_openai_response, from_anthropic_response

    file_path = Path(file)
    if not file_path.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(code=1)

    data = json.loads(file_path.read_text())

    valid_sources = ("atir", "openai", "anthropic")
    if source not in valid_sources:
        console.print(f"[red]Unknown source '{source}'. Choose from: {valid_sources}[/red]")
        raise typer.Exit(code=1)

    try:
        if source == "atir":
            trace = from_dict(data)
        elif source == "openai":
            trace = from_openai_response(data, task=data.get("task", ""), agent_name="imported_agent")
        elif source == "anthropic":
            trace = from_anthropic_response(data, task=data.get("task", ""), agent_name="imported_agent")
    except Exception as exc:
        console.print(f"[red]Import failed: {exc}[/red]")
        raise typer.Exit(code=1)

    if show:
        agg = trace.aggregates
        table = Table(title="Imported Trace", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Agent", trace.agent_name)
        table.add_row("Provider", trace.provider)
        table.add_row("ATIR version", trace.atir_version)
        table.add_row("Spans", str(len(trace.spans)))
        if agg:
            table.add_row("Total tokens", str(agg.total_tokens))
            table.add_row("Total cost", f"${agg.total_cost_usd:.6f}")
            table.add_row("LLM calls", str(agg.llm_calls))
            table.add_row("Tool calls", str(agg.tool_calls))
        console.print(table)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(trace.to_dict(), indent=2))
        console.print(f"[green]Trace saved → {output}[/green]")
    else:
        console.print("[dim]Tip: use --output to save to a file[/dim]")


@app.command(name="instrument")
def instrument_script(
    script: str = typer.Argument(..., help="Python script to run with auto-instrumentation"),
    agent_name: str = typer.Option("instrumented_agent", "--agent", "-a", help="Agent name for the trace"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Save ATIR trace to this path"),
    providers: str = typer.Option(
        "anthropic,openai",
        "--providers",
        "-p",
        help="Comma-separated LLM providers to instrument: anthropic,openai",
    ),
):
    """Run a Python script with automatic LLM call instrumentation.

    All LLM API calls in the script are automatically captured and output
    as an ATIR trace::

        runcore instrument my_agent.py --agent my_agent --output trace.atir.json
    """
    import importlib.util
    import sys
    from runcore.sdk import auto_instrument, capture

    script_path = Path(script)
    if not script_path.exists():
        console.print(f"[red]Script not found: {script}[/red]")
        raise typer.Exit(code=1)

    provider_list = [p.strip() for p in providers.split(",") if p.strip()]
    patched = auto_instrument(frameworks=provider_list)
    patched_names = [k for k, v in patched.items() if v]
    console.print(f"[bold]Instrumented:[/bold] {', '.join(patched_names) or 'none'}")

    cap = capture(agent_name=agent_name, task=f"script: {script_path.name}")
    with cap:
        spec = importlib.util.spec_from_file_location("__instrumented__", str(script_path))
        mod = importlib.util.module_from_spec(spec)
        sys.argv = [str(script_path)]
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        except Exception as exc:
            console.print(f"[yellow]Script exited with error: {exc}[/yellow]")
            cap.set_success(False)

    trace = cap.get_atir()
    agg = trace.aggregates

    table = Table(title=f"Instrumented Run: {script_path.name}", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("LLM calls captured", str(agg.llm_calls if agg else 0))
    table.add_row("Tool calls captured", str(agg.tool_calls if agg else 0))
    table.add_row("Total tokens", str(agg.total_tokens if agg else 0))
    table.add_row("Total cost", f"${agg.total_cost_usd:.6f}" if agg else "$0.000000")
    console.print(table)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(trace.to_dict(), indent=2))
        console.print(f"[green]ATIR trace saved → {output}[/green]")
    else:
        console.print("[dim]Tip: use --output to save the ATIR trace[/dim]")


@app.command(name="compare-providers")
def compare_providers(
    task: str = typer.Argument(
        "Summarize the following in one sentence: The quick brown fox jumps over the lazy dog.",
        help="Task to run on all available providers",
    ),
    runs: int = typer.Option(3, "--runs", "-r", help="Runs per task per provider"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Save JSON leaderboard to this path"),
):
    """Benchmark all available providers head-to-head, ranked by CpST.

    Reads API keys from environment variables.
    Providers without keys are skipped automatically.

    Example::

        export ANTHROPIC_API_KEY=sk-ant-...
        export OPENAI_API_KEY=sk-...
        runcore compare-providers "Classify: 'great product!' → positive/negative" --runs 5
    """
    from runcore.benchmark.provider_bench import ProviderBench, ProviderConfig

    bench = ProviderBench(
        tasks=[task],
        system_prompt="You are a helpful assistant. Be concise.",
    )

    # Add all well-known providers — skip automatically if key missing
    bench.add_provider(ProviderConfig(
        label="Claude Haiku 4.5",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key_env="ANTHROPIC_API_KEY",
    ))
    bench.add_provider(ProviderConfig(
        label="Claude Sonnet 4.6",
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
    ))
    bench.add_provider(ProviderConfig(
        label="GPT-4o-mini",
        provider="openai",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    ))
    bench.add_provider(ProviderConfig(
        label="GPT-4o",
        provider="openai",
        model="gpt-4o",
        api_key_env="OPENAI_API_KEY",
    ))

    console.print(f"[bold]Comparing providers[/bold] · {runs} runs · task: [italic]{task[:60]}[/italic]")
    console.print("[dim]Providers without API keys will be skipped automatically.[/dim]\n")

    results = bench.run(runs_per_task=runs)
    bench.print_leaderboard(results)

    # Rich table
    table = Table(title="Provider CpST Leaderboard", box=box.ROUNDED)
    table.add_column("Rank", style="cyan", width=5)
    table.add_column("Provider", style="white")
    table.add_column("Model", style="dim")
    table.add_column("CpST", justify="right")
    table.add_column("Avg cost", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("OK%", justify="right")

    for i, r in enumerate(results, 1):
        if r.error:
            table.add_row(str(i), r.label, "—", "—", "—", "—", f"[dim]{r.error[:30]}[/dim]")
        else:
            rank_str = f"[bold green]{i}[/bold green]" if i == 1 else str(i)
            table.add_row(
                rank_str, r.label, r.model,
                f"[{'green' if i==1 else 'white'}]${r.avg_cpst:.5f}[/{'green' if i==1 else 'white'}]",
                f"${r.avg_cost:.5f}",
                f"{r.avg_latency_ms:.0f}ms",
                f"{r.success_rate*100:.0f}%",
            )
    console.print(table)

    if output:
        import json as _json
        leaderboard = [r.to_dict() for r in results]
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(_json.dumps({"leaderboard": leaderboard, "task": task, "runs_per_provider": runs}, indent=2))
        console.print(f"Leaderboard saved → [cyan]{output}[/cyan]")


@app.command()
def watch(
    source: str = typer.Option(
        ".runcore/traces",
        "--source",
        "-s",
        help="Directory of .atir.json trace files to watch (or 'reports' to use .runcore/reports/)",
    ),
    interval: float = typer.Option(
        30.0, "--interval", "-i", help="Polling interval in seconds"
    ),
    window: int = typer.Option(
        20, "--window", "-w", help="Number of recent traces per check window"
    ),
    cpst_warn: float = typer.Option(
        20.0, "--cpst-warn", help="CpST degradation % to trigger WARNING"
    ),
    cpst_crit: float = typer.Option(
        50.0, "--cpst-crit", help="CpST degradation % to trigger CRITICAL"
    ),
    loop_warn: float = typer.Option(
        0.20, "--loop-warn", help="Loop risk score to trigger WARNING"
    ),
    slack: Optional[str] = typer.Option(
        None, "--slack", help="Slack incoming webhook URL for alerts"
    ),
    webhook: Optional[str] = typer.Option(
        None, "--webhook", help="Generic HTTP webhook URL for alerts"
    ),
    once: bool = typer.Option(
        False, "--once", help="Run a single check and exit (no daemon loop)"
    ),
):
    """Watch a trace directory for CpST drift and loop risk alerts.

    Runs continuously (daemon mode) or once with --once.

    Examples::

        # Watch default trace dir, check every 30s
        runcore watch

        # Watch reports dir once and exit
        runcore watch --source reports --once

        # Alert to Slack when CpST degrades >25%
        runcore watch --cpst-warn 25 --slack https://hooks.slack.com/...
    """
    from runcore.monitor import (
        MonitorConfig, MonitorDaemon, FileTraceSource, RunCoreReportSource
    )

    config = MonitorConfig(
        cpst_warning_threshold_pct=cpst_warn,
        cpst_critical_threshold_pct=cpst_crit,
        loop_risk_warning=loop_warn,
        poll_interval_seconds=interval,
        webhook_url=webhook,
        slack_webhook_url=slack,
        window_size=window,
    )

    if source == "reports":
        trace_source = RunCoreReportSource(
            reports_dir=".runcore/reports", window=window
        )
    else:
        source_path = Path(source)
        source_path.mkdir(parents=True, exist_ok=True)
        trace_source = FileTraceSource(directory=source_path, window=window)

    daemon = MonitorDaemon(trace_source=trace_source, config=config)

    console.print(Panel.fit(
        f"[bold green]RunCore Monitor[/bold green]\n"
        f"Source:    [cyan]{source}[/cyan]\n"
        f"Interval:  [cyan]{interval}s[/cyan]\n"
        f"Window:    [cyan]{window} traces[/cyan]\n"
        f"CpST warn: [yellow]>{cpst_warn:.0f}% degradation[/yellow]\n"
        f"Loop warn: [yellow]>{loop_warn:.2f}[/yellow]\n"
        f"{'Slack: ' + slack if slack else '[dim]No Slack webhook[/dim]'}\n"
        f"[dim]Ctrl+C to stop[/dim]",
        title="[bold purple]RunCore Watch[/bold purple]",
    ))

    if once:
        snapshot = daemon.tick_once()
        if snapshot is None:
            console.print("[yellow]No traces found or not enough data for baseline.[/yellow]")
            return
        table = Table(title="Monitor Snapshot", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Agent", snapshot.agent_name)
        table.add_row("Traces in window", str(snapshot.window_traces))
        table.add_row("Avg CpST", f"${snapshot.avg_cpst:.5f}")
        table.add_row("Avg loop risk", f"{snapshot.avg_loop_risk:.3f}")
        table.add_row("Success rate", f"{snapshot.success_rate*100:.0f}%")
        table.add_row("Avg quality", f"{snapshot.avg_quality:.3f}")
        table.add_row("Alerts", str(len(snapshot.alerts)))
        console.print(table)
        if snapshot.alerts:
            for alert in snapshot.alerts:
                color = {"critical": "red", "warning": "yellow", "info": "blue"}.get(alert.severity.value, "white")
                console.print(f"  [{color}][{alert.severity.value.upper()}][/{color}] {alert.message}")
    else:
        daemon.run()


@app.command()
def certify(
    provider: str = typer.Option("groq", "--provider", "-p", help="LLM provider: groq | gemini | ollama"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override model name"),
    runs: int = typer.Option(5, "--runs", "-r", help="Runs per task (5 = standard certification, 10 = enterprise-grade CI)"),
    suite: str = typer.Option("all", "--suite", "-s", help="Task suite: support | research | coding | analytics | all"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Save HTML report to this path"),
    open_report: bool = typer.Option(True, "--open/--no-open", help="Open HTML report in browser when done"),
):
    """Run the RunCore certification suite and generate a signed score report.

    Measures real cost savings (baseline vs guarded) across N runs per task.
    Produces a RunCore Score™ (0–100) with 95% confidence interval and
    a tamper-evident SHA-256 fingerprint.

    To reproduce the result on any machine::

        runcore certify --provider groq --runs 5

    Requires the provider's API key in the environment:
        GROQ_API_KEY, GEMINI_API_KEY, or Ollama running locally.
    """
    import os
    import webbrowser
    from pathlib import Path as _Path

    # Check provider availability
    provider_key_map = {
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    if provider in provider_key_map:
        key_var = provider_key_map[provider]
        if not os.environ.get(key_var):
            console.print(f"[red]{key_var} not set.[/red]")
            console.print(f"Get a free key at: [cyan]{'https://console.groq.com' if provider == 'groq' else 'https://aistudio.google.com'}[/cyan]")
            raise typer.Exit(1)
    elif provider == "ollama":
        try:
            import httpx
            httpx.get("http://localhost:11434/api/tags", timeout=2)
        except Exception:
            console.print("[red]Ollama not reachable at localhost:11434.[/red]")
            console.print("Start it with: [cyan]ollama serve[/cyan]")
            raise typer.Exit(1)
        # Warn about models known to lack native tool calling
        _no_tools = {"mistral", "mistral:7b", "mistral:latest", "llama3.2", "llama3.2:latest",
                     "phi3", "phi3:latest", "gemma2:2b", "gemma2"}
        _m = (model or "").lower()
        if _m in _no_tools or any(_m.startswith(x) for x in ("mistral:", "phi3:", "gemma2:")):
            console.print(f"[yellow]Warning: {model} may not support native tool calling.[/yellow]")
            console.print("[yellow]Recommended models with tool calling: llama3.1:8b, qwen2.5:7b, llama3.3:70b[/yellow]")

    import sys, os as _os
    _repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    if _repo not in sys.path:
        sys.path.insert(0, _repo)
    from benchmarks.tasks import ALL_TASKS
    suite_tasks = [t for ts in ALL_TASKS.values() for t in ts] if suite == "all" else ALL_TASKS.get(suite, [])
    n_llm_calls = len(suite_tasks) * runs * 2

    console.print(Panel.fit(
        f"[bold]RunCore Certification[/bold]\n\n"
        f"Provider   [cyan]{provider}[/cyan]  {'(' + model + ')' if model else ''}\n"
        f"Suite      [cyan]{suite}[/cyan]  ({len(suite_tasks)} tasks)\n"
        f"Runs/task  [cyan]{runs}[/cyan]\n"
        f"LLM calls  [cyan]{n_llm_calls}[/cyan]  (baseline + guarded × {runs})\n\n"
        f"[dim]This may take a few minutes depending on provider speed.[/dim]",
        title="[bold blue]RunCore Score™[/bold blue]",
    ))

    try:
        from benchmarks.certification import run_certification, save_cert
    except ImportError:
        console.print("[red]benchmarks package not found. Run from the RunCore project root.[/red]")
        raise typer.Exit(1)

    with console.status(f"[bold blue]Running certification ({n_llm_calls} LLM calls)…[/bold blue]"):
        score = run_certification(
            provider_name=provider,
            model=model,
            runs_per_task=runs,
            suite=suite,
            verbose=False,
        )

    # Print score
    grade_colors = {"A+": "bold green", "A": "green", "B+": "cyan", "B": "blue", "C": "yellow", "F": "red"}
    grade_color = grade_colors.get(score.grade, "white")
    cert_color = "green" if score.certified else "red"
    cert_icon = "✅" if score.certified else "❌"

    console.print()
    console.print(Panel(
        f"[{grade_color}]{score.overall:.1f}[/{grade_color}] [dim]/100[/dim]  "
        f"[{grade_color}]Grade {score.grade}[/{grade_color}]  "
        f"[{cert_color}]{cert_icon} {'RunCore Certified' if score.certified else 'Not Certified'}[/{cert_color}]\n\n"
        f"[dim]95% CI: [{score.confidence_interval_95[0]:.1f} — {score.confidence_interval_95[1]:.1f}]  "
        f"· {score.n_runs} runs · {score.n_tasks} tasks[/dim]",
        title="[bold]RunCore Score™[/bold]",
    ))

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    table.add_column("Dimension", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Improvement", justify="right")
    table.add_column("Target", justify="right")
    table.add_column("", justify="left")

    for d in score.dimensions:
        passed_str = "[green]✓ PASS[/green]" if d.passed else "[yellow]⚠ MISS[/yellow]"
        table.add_row(
            d.name,
            f"{d.score:.0f}/100",
            f"+{d.improvement_pct:.1f}%" if d.improvement_pct >= 0 else f"{d.improvement_pct:.1f}%",
            f"{d.target_pct:.0f}%" if d.target_pct > 0 else "—",
            passed_str,
        )
    console.print(table)

    out_path = save_cert(score, _Path(output) if output else None)
    console.print(f"[bold]Report saved →[/bold] [cyan]{out_path}[/cyan]")
    console.print(f"[bold]Score JSON  →[/bold] [cyan]{out_path.with_suffix('.json')}[/cyan]")
    console.print(f"[dim]Fingerprint: {__import__('hashlib').sha256(__import__('json').dumps({'overall': score.overall, 'provider': score.provider, 'model': score.model, 'n_runs': score.n_runs, 'timestamp': score.timestamp}, sort_keys=True).encode()).hexdigest()[:16].upper()}[/dim]")

    if open_report:
        webbrowser.open(out_path.as_uri())

    raise typer.Exit(0 if score.certified else 1)


if __name__ == "__main__":
    app()
