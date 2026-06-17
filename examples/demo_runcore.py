#!/usr/bin/env python3
"""
RunCore Demo — Before vs. After comparison with real CpST numbers.

Run with:
    python3 examples/demo_runcore.py

No external API keys required — uses RunCore's built-in simulated agents.
"""

from __future__ import annotations

import json
import sys
import os

# Ensure the project root is on sys.path when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text

from runcore.agents.simulated import SimulatedAgentFactory
from runcore.benchmark.runner import BenchmarkRunner
from runcore.benchmark.metrics import calculate_metrics
from runcore.core.models import OptimizationConfig
from runcore.advisor import OptimizationAdvisor
from runcore.atir.converter import agent_trace_to_atir
from runcore.sdk import capture, GuardConfig

console = Console()

TASKS = [
    "Process refund for order INV-1001",
    "Check invoice status for customer@example.com",
    "Refund $99.99 for order INV-1001 — duplicate billing",
    "Look up customer account and issue refund",
    "Customer says they never received order, process refund",
]

# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Without RunCore
# ─────────────────────────────────────────────────────────────────────────────

def run_baseline() -> list:
    console.print(Panel(
        "[bold white]Section 1: Without RunCore[/bold white]\n"
        "Running 5 tasks through the support agent with [red]no optimizations[/red].",
        border_style="red",
    ))

    factory = SimulatedAgentFactory()
    agent = factory.create("support")
    runner = BenchmarkRunner()

    with console.status("[red]Running baseline tasks...[/red]"):
        traces = runner.run_baseline(agent, TASKS, runs_per_task=1)

    metrics = calculate_metrics(traces)

    table = Table(title="Baseline Results (No RunCore)", box=box.ROUNDED, border_style="red")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Runs", str(metrics.runs))
    table.add_row("Avg Cost / Run", f"${metrics.avg_cost:.5f}")
    table.add_row("CpST (Cost per Successful Task)", f"[bold red]${metrics.cost_per_successful_task:.5f}[/bold red]")
    table.add_row("Avg Tokens / Run", f"{metrics.avg_tokens:,}")
    table.add_row("Success Rate", f"{metrics.success_rate * 100:.1f}%")
    table.add_row("Avg Latency (ms)", f"{metrics.avg_latency_ms:.0f}")
    table.add_row("Avg Tool Calls / Run", f"{metrics.avg_tool_calls:.1f}")

    console.print(table)
    console.print()
    return traces, metrics


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: With RunCore Guards
# ─────────────────────────────────────────────────────────────────────────────

def run_with_guards(baseline_traces: list) -> tuple:
    console.print(Panel(
        "[bold white]Section 2: With RunCore Guards[/bold white]\n"
        "Same tasks with [green]GuardConfig(dedup_scope=\"session\")[/green] — "
        "duplicate tool calls blocked at runtime.",
        border_style="green",
    ))

    factory = SimulatedAgentFactory()
    agent = factory.create("support")

    guarded_traces = []
    total_blocked = 0
    total_tokens_saved = 0
    total_cost_saved = 0.0

    with console.status("[green]Running guarded tasks...[/green]"):
        for task in TASKS:
            with capture(
                "support_agent",
                task=task,
                guards=GuardConfig(dedup_scope="session"),
            ) as cap:
                # Manually simulate what the support agent does,
                # but route tool calls through the guard-aware capture
                _CUSTOMER_RESULT = {
                    "id": "cust_42", "name": "John Doe",
                    "email": "customer@example.com", "tier": "standard",
                }
                _INVOICE_RESULT = {
                    "id": "INV-1001", "amount": 99.99, "status": "paid",
                    "refund_eligible": True, "days_since_purchase": 6,
                }

                # Turn 1 — initial reasoning
                cap.new_turn()
                cap.record_llm(
                    provider="simulated", model="simulated-agent-v1",
                    input_tokens=520, output_tokens=95,
                    cost_usd=0.000180, duration_ms=310,
                )

                # Tool: search_docs (may be duplicate across session)
                try:
                    cap.record_tool("search_docs",
                        {"query": "refund policy eligibility requirements"},
                        {"results": ["Refund Policy: 30-day window."]},
                        True, 22.0, input_tokens=40)
                except Exception:
                    pass  # guard blocked it

                # Tool: get_customer
                try:
                    cap.record_tool("get_customer",
                        {"email": "customer@example.com"},
                        _CUSTOMER_RESULT, True, 18.0, input_tokens=35)
                except Exception:
                    pass

                # Tool: get_invoice
                try:
                    cap.record_tool("get_invoice",
                        {"invoice_id": "INV-1001"},
                        _INVOICE_RESULT, True, 15.0, input_tokens=30)
                except Exception:
                    pass

                # Turn 2 — reasoning before refund
                cap.new_turn()
                cap.record_llm(
                    provider="simulated", model="simulated-agent-v1",
                    input_tokens=780, output_tokens=120,
                    cost_usd=0.000270, duration_ms=380,
                )

                # Duplicate get_invoice — guard will block if session-scoped
                try:
                    cap.record_tool("get_invoice",
                        {"invoice_id": "INV-1001"},
                        _INVOICE_RESULT, True, 15.0, input_tokens=30)
                except Exception:
                    pass

                # Duplicate search_docs — guard will block
                try:
                    cap.record_tool("search_docs",
                        {"query": "refund policy eligibility requirements"},
                        {"results": ["Refund Policy: 30-day window."]},
                        True, 22.0, input_tokens=40)
                except Exception:
                    pass

                # Actual refund
                try:
                    cap.record_tool("refund_order",
                        {"order_id": "INV-1001", "amount": 99.99},
                        {"status": "refunded", "ref": "REF-9901"}, True, 45.0, input_tokens=50)
                except Exception:
                    pass

                # Turn 3 — final response
                cap.record_llm(
                    provider="simulated", model="simulated-agent-v1",
                    input_tokens=420, output_tokens=85,
                    cost_usd=0.000150, duration_ms=280,
                )
                cap.set_success(True)
                cap.set_quality(0.95)

            atir_trace = cap.get_atir()
            guarded_traces.append(cap.get_trace())

            sr = cap.savings_report()
            if sr:
                total_blocked += sr.blocked_tool_calls
                total_tokens_saved += sr.total_tokens_saved
                total_cost_saved += sr.total_cost_saved_usd

    guarded_metrics = calculate_metrics(guarded_traces)

    table = Table(title="Guarded Results (With RunCore)", box=box.ROUNDED, border_style="green")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Runs", str(guarded_metrics.runs))
    table.add_row("Avg Cost / Run", f"${guarded_metrics.avg_cost:.5f}")
    table.add_row("CpST (Cost per Successful Task)", f"[bold green]${guarded_metrics.cost_per_successful_task:.5f}[/bold green]")
    table.add_row("Avg Tokens / Run", f"{guarded_metrics.avg_tokens:,}")
    table.add_row("Success Rate", f"{guarded_metrics.success_rate * 100:.1f}%")
    table.add_row("Avg Latency (ms)", f"{guarded_metrics.avg_latency_ms:.0f}")
    table.add_row("[green]Duplicate Calls Blocked[/green]", f"[green]{total_blocked}[/green]")
    table.add_row("[green]Tokens Saved (guards)[/green]", f"[green]{total_tokens_saved:,}[/green]")
    table.add_row("[green]Cost Saved (guards)[/green]", f"[green]${total_cost_saved:.5f}[/green]")

    console.print(table)
    console.print()
    return guarded_traces, guarded_metrics


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Optimization Advisor
# ─────────────────────────────────────────────────────────────────────────────

def run_advisor(baseline_traces: list) -> None:
    console.print(Panel(
        "[bold white]Section 3: Optimization Advisor[/bold white]\n"
        "Analyzing baseline traces and generating ranked prescriptions.",
        border_style="yellow",
    ))

    atir_traces = [agent_trace_to_atir(t) for t in baseline_traces]

    advisor = OptimizationAdvisor()
    report = advisor.analyze(atir_traces, agent_name="support_agent")

    console.print(f"[bold]Analyzed [yellow]{report.traces_analyzed}[/yellow] traces — "
                  f"found [yellow]{len(report.prescriptions)}[/yellow] prescriptions[/bold]\n")

    top3 = report.prescriptions[:3]
    for i, p in enumerate(top3, 1):
        effort_color = {"low": "green", "medium": "yellow", "high": "red"}.get(
            p.effort.value, "white"
        )
        console.print(
            f"  [bold cyan]#{i}[/bold cyan] [bold]{p.title}[/bold]\n"
            f"       Savings: [yellow]~{p.estimated_savings_pct:.1f}%[/yellow]  "
            f"Confidence: [white]{p.confidence:.0%}[/white]  "
            f"Effort: [{effort_color}]{p.effort.value}[/{effort_color}]\n"
            f"       {p.description[:120]}...\n"
        )

    console.print(
        f"[bold]Combined estimated savings: "
        f"[yellow]{report.total_estimated_savings_pct():.1f}%[/yellow] "
        f"(${report.total_estimated_savings_usd():.5f}/run)[/bold]\n"
    )
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Summary Table
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(baseline_metrics, guarded_metrics) -> None:
    console.print(Panel(
        "[bold white]Section 4: Before vs. After Summary[/bold white]",
        border_style="blue",
    ))

    cpst_baseline = baseline_metrics.cost_per_successful_task
    cpst_guarded  = guarded_metrics.cost_per_successful_task
    savings_pct = (
        (cpst_baseline - cpst_guarded) / cpst_baseline * 100
        if cpst_baseline > 0 else 0.0
    )
    token_savings_pct = (
        (baseline_metrics.avg_tokens - guarded_metrics.avg_tokens)
        / baseline_metrics.avg_tokens * 100
        if baseline_metrics.avg_tokens > 0 else 0.0
    )

    table = Table(
        title="RunCore Impact Summary",
        box=box.DOUBLE_EDGE,
        border_style="blue",
        show_lines=True,
    )
    table.add_column("Metric", style="bold", min_width=30)
    table.add_column("Without RunCore", justify="right", style="red")
    table.add_column("With RunCore", justify="right", style="green")
    table.add_column("Delta", justify="right", style="bold yellow")

    def delta(before, after, fmt=".5f", lower_better=True):
        diff = after - before
        pct = diff / before * 100 if before != 0 else 0.0
        arrow = "↓" if diff < 0 else "↑"
        color = "green" if (diff < 0) == lower_better else "red"
        return f"[{color}]{arrow}{abs(pct):.1f}%[/{color}]"

    table.add_row(
        "CpST (Cost per Successful Task)",
        f"${cpst_baseline:.5f}",
        f"${cpst_guarded:.5f}",
        delta(cpst_baseline, cpst_guarded),
    )
    table.add_row(
        "Avg Cost / Run",
        f"${baseline_metrics.avg_cost:.5f}",
        f"${guarded_metrics.avg_cost:.5f}",
        delta(baseline_metrics.avg_cost, guarded_metrics.avg_cost),
    )
    table.add_row(
        "Avg Tokens / Run",
        f"{baseline_metrics.avg_tokens:,}",
        f"{guarded_metrics.avg_tokens:,}",
        delta(baseline_metrics.avg_tokens, guarded_metrics.avg_tokens, lower_better=True),
    )
    table.add_row(
        "Success Rate",
        f"{baseline_metrics.success_rate * 100:.1f}%",
        f"{guarded_metrics.success_rate * 100:.1f}%",
        delta(baseline_metrics.success_rate, guarded_metrics.success_rate, lower_better=False),
    )
    table.add_row(
        "Avg Latency (ms)",
        f"{baseline_metrics.avg_latency_ms:.0f}",
        f"{guarded_metrics.avg_latency_ms:.0f}",
        delta(baseline_metrics.avg_latency_ms, guarded_metrics.avg_latency_ms),
    )

    console.print(table)
    console.print()

    console.print(
        f"[bold green]CpST improvement: {savings_pct:.1f}%[/bold green]  |  "
        f"[bold green]Token reduction: {token_savings_pct:.1f}%[/bold green]\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Save ATIR trace
# ─────────────────────────────────────────────────────────────────────────────

def save_trace(baseline_traces: list) -> str:
    atir_traces = [agent_trace_to_atir(t) for t in baseline_traces]
    output = []
    for t in atir_traces:
        output.append({
            "trace_id": t.trace_id,
            "agent_name": t.agent_name,
            "task": t.task,
            "success": t.success,
            "aggregates": {
                "total_cost_usd": t.aggregates.total_cost_usd if t.aggregates else 0,
                "total_tokens": t.aggregates.total_tokens if t.aggregates else 0,
                "llm_calls": t.aggregates.llm_calls if t.aggregates else 0,
                "tool_calls": t.aggregates.tool_calls if t.aggregates else 0,
                "duplicate_tool_calls": t.aggregates.duplicate_tool_calls if t.aggregates else 0,
                "cost_per_successful_task": t.aggregates.cost_per_successful_task if t.aggregates else 0,
            },
            "spans": [
                {
                    "type": s.type,
                    "span_id": s.span_id,
                    **({"model": s.model, "provider": s.provider,
                        "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
                        "cost_usd": s.cost_usd} if s.type == "llm_call" else
                       {"name": s.name, "success": s.success,
                        "arguments": s.arguments, "result_summary": s.result_summary}),
                }
                for s in t.spans
            ],
        })

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo_trace.json")
    path = os.path.normpath(path)
    with open(path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    console.print(Panel(
        "[bold cyan]RunCore — Production AI Agent Optimization[/bold cyan]\n"
        "[dim]CpST · ATIR · Guards · OptimizationAdvisor[/dim]",
        border_style="cyan",
        expand=False,
    ))
    console.print()

    # Section 1
    baseline_traces, baseline_metrics = run_baseline()

    # Section 2
    guarded_traces, guarded_metrics = run_with_guards(baseline_traces)

    # Section 3
    run_advisor(baseline_traces)

    # Section 4
    print_summary(baseline_metrics, guarded_metrics)

    # Save trace
    trace_path = save_trace(baseline_traces)
    console.print(f"[dim]Full trace saved to: {trace_path}[/dim]")


if __name__ == "__main__":
    main()
