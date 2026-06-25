"""RunCore benchmark runner — runs baseline vs guarded for all tasks, records everything.

Usage:
    python benchmarks/runner.py --provider groq --suite support
    python benchmarks/runner.py --provider ollama --model llama3.2 --suite all
    python benchmarks/runner.py --provider gemini --suite research --runs 3
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runcore
from runcore import GuardConfig
from runcore.advisor import OptimizationAdvisor
from benchmarks.tasks import ALL_TASKS, BenchmarkTask
from benchmarks.agents.base import BaseAgent, AgentRun

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _build_provider(name: str, model: str | None):
    if name == "groq":
        from runcore.providers.groq import GroqProvider
        return GroqProvider(model=model or "llama-3.1-8b-instant")
    elif name == "gemini":
        from runcore.providers.gemini import GeminiProvider
        return GeminiProvider(model=model or "gemini-1.5-flash-8b")
    elif name == "ollama":
        from runcore.providers.ollama import OllamaProvider
        return OllamaProvider(model=model or "llama3.2")
    else:
        raise ValueError(f"Unknown provider: {name}. Choose groq | gemini | ollama")


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------

def run_one(
    task: BenchmarkTask,
    provider_name: str,
    model: str | None,
    with_guards: bool,
) -> tuple[AgentRun, runcore.atir.spec.ATIRTrace]:
    provider = _build_provider(provider_name, model)
    guards = GuardConfig(
        dedup_enabled=True,
        loop_break_enabled=True,
        context_compression_enabled=True,
    ) if with_guards else None

    agent = BaseAgent(provider=provider, guards=guards)

    label = "guarded" if with_guards else "baseline"
    with runcore.capture(
        agent_name=f"{task.id}__{label}",
        task=task.user_message,
        framework=provider_name,
        guards=guards,
    ) as cap:
        run = agent.run(task, cap)

    trace = cap.get_atir()
    return run, trace


# ---------------------------------------------------------------------------
# Full benchmark suite
# ---------------------------------------------------------------------------

def run_suite(
    suite_name: str,
    provider_name: str,
    model: str | None,
    runs_per_task: int = 1,
    verbose: bool = True,
) -> dict:
    tasks = ALL_TASKS.get(suite_name, [])
    if not tasks:
        available = list(ALL_TASKS.keys())
        raise ValueError(f"Unknown suite: {suite_name}. Available: {available}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_DIR / f"{timestamp}_{provider_name}_{suite_name}"
    run_dir.mkdir(parents=True)

    baseline_traces = []
    guarded_traces = []
    baseline_runs = []
    guarded_runs = []

    for task in tasks:
        if verbose:
            print(f"\n{'='*60}")
            print(f"Task: {task.name} [{task.id}]")
            print(f"{'='*60}")

        for r in range(runs_per_task):
            if verbose:
                print(f"  Run {r+1}/{runs_per_task} — baseline...")

            try:
                brun, btrace = run_one(task, provider_name, model, with_guards=False)
                baseline_traces.append(btrace)
                baseline_runs.append(brun)

                # Save trace
                trace_path = run_dir / f"{task.id}_baseline_{r}.json"
                trace_path.write_text(
                    json.dumps(btrace.model_dump(mode="json"), indent=2, default=str)
                )
                brun.trace_path = str(trace_path)

                if verbose:
                    agg = btrace.aggregates
                    print(f"    CpST:      ${agg.cost_per_successful_task:.6f}")
                    print(f"    Cost:      ${agg.total_cost_usd:.6f}")
                    print(f"    Tokens:    {agg.total_tokens}")
                    print(f"    Tools:     {agg.tool_calls} ({agg.duplicate_tool_calls} dups)")
                    print(f"    Success:   {brun.success}")

            except Exception as e:
                if verbose:
                    print(f"    ERROR (baseline): {e}")

            if verbose:
                print(f"  Run {r+1}/{runs_per_task} — guarded...")

            try:
                grun, gtrace = run_one(task, provider_name, model, with_guards=True)
                guarded_traces.append(gtrace)
                guarded_runs.append(grun)

                trace_path = run_dir / f"{task.id}_guarded_{r}.json"
                trace_path.write_text(
                    json.dumps(gtrace.model_dump(mode="json"), indent=2, default=str)
                )
                grun.trace_path = str(trace_path)

                if verbose:
                    agg = gtrace.aggregates
                    savings = cap_savings(gtrace)
                    print(f"    CpST:      ${agg.cost_per_successful_task:.6f}")
                    print(f"    Cost:      ${agg.total_cost_usd:.6f}")
                    print(f"    Tokens:    {agg.total_tokens}")
                    print(f"    Tools:     {agg.tool_calls} ({agg.duplicate_tool_calls} dups)")
                    if savings:
                        print(f"    Savings:   {savings}")

            except Exception as e:
                if verbose:
                    print(f"    ERROR (guarded): {e}")

    # Run OptimizationAdvisor on baseline traces
    advisor_report = None
    if baseline_traces:
        try:
            advisor = OptimizationAdvisor()
            advisor_report = advisor.analyze(baseline_traces, agent_name=f"{suite_name}_agent")
        except Exception as e:
            if verbose:
                print(f"\nAdvisor error: {e}")

    # Compute summary
    summary = _compute_summary(baseline_runs, guarded_runs, baseline_traces, guarded_traces, advisor_report)
    summary["provider"] = provider_name
    summary["model"] = model
    summary["suite"] = suite_name
    summary["timestamp"] = timestamp
    summary["results_dir"] = str(run_dir)

    # Save summary
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))

    if verbose:
        _print_summary(summary)

    return summary


def cap_savings(trace) -> str | None:
    """Extract savings info from trace if present."""
    if trace.savings:
        s = trace.savings
        blocked = s.get("blocked_tool_calls", 0)
        cost_saved = s.get("blocked_tool_calls_cost_usd", 0) + s.get("cost_saved_compression_usd", 0)
        if blocked or cost_saved:
            return f"{blocked} calls blocked, ${cost_saved:.6f} saved"
    return None


def _compute_summary(baseline_runs, guarded_runs, baseline_traces, guarded_traces, advisor_report) -> dict:
    def avg(lst, key):
        vals = [getattr(r, key) for r in lst if hasattr(r, key)]
        return sum(vals) / len(vals) if vals else 0

    def avg_agg(traces, field):
        vals = [getattr(t.aggregates, field, 0) for t in traces if t.aggregates]
        return sum(vals) / len(vals) if vals else 0

    b_cpst = avg_agg(baseline_traces, "cost_per_successful_task")
    g_cpst = avg_agg(guarded_traces, "cost_per_successful_task")
    b_tokens = avg_agg(baseline_traces, "total_tokens")
    g_tokens = avg_agg(guarded_traces, "total_tokens")
    b_cost = avg_agg(baseline_traces, "total_cost_usd")
    g_cost = avg_agg(guarded_traces, "total_cost_usd")
    b_dups = sum(t.aggregates.duplicate_tool_calls for t in baseline_traces if t.aggregates)
    g_dups = sum(t.aggregates.duplicate_tool_calls for t in guarded_traces if t.aggregates)

    cpst_reduction = ((b_cpst - g_cpst) / b_cpst * 100) if b_cpst > 0 else 0
    token_reduction = ((b_tokens - g_tokens) / b_tokens * 100) if b_tokens > 0 else 0
    cost_reduction = ((b_cost - g_cost) / b_cost * 100) if b_cost > 0 else 0

    b_success = sum(1 for r in baseline_runs if r.success) / max(1, len(baseline_runs)) * 100
    g_success = sum(1 for r in guarded_runs if r.success) / max(1, len(guarded_runs)) * 100

    summary = {
        "baseline": {
            "avg_cpst": round(b_cpst, 8),
            "avg_cost_usd": round(b_cost, 8),
            "avg_tokens": round(b_tokens, 1),
            "total_duplicates": b_dups,
            "success_rate_pct": round(b_success, 1),
            "runs": len(baseline_runs),
        },
        "guarded": {
            "avg_cpst": round(g_cpst, 8),
            "avg_cost_usd": round(g_cost, 8),
            "avg_tokens": round(g_tokens, 1),
            "total_duplicates": g_dups,
            "success_rate_pct": round(g_success, 1),
            "runs": len(guarded_runs),
        },
        "improvements": {
            "cpst_reduction_pct": round(cpst_reduction, 1),
            "token_reduction_pct": round(token_reduction, 1),
            "cost_reduction_pct": round(cost_reduction, 1),
            "duplicates_blocked": b_dups - g_dups,
        },
        "advisor": advisor_report.to_dict() if advisor_report else None,
    }
    return summary


def _print_summary(s: dict):
    b = s["baseline"]
    g = s["guarded"]
    imp = s["improvements"]

    print(f"\n{'='*60}")
    print(f"BENCHMARK SUMMARY — {s.get('provider','?')} / {s.get('suite','?')}")
    print(f"{'='*60}")
    print(f"{'Metric':<28} {'Baseline':>12} {'Guarded':>12} {'Δ':>10}")
    print(f"{'-'*60}")
    print(f"{'Avg CpST':<28} ${b['avg_cpst']:>11.6f} ${g['avg_cpst']:>11.6f} {imp['cpst_reduction_pct']:>+9.1f}%")
    print(f"{'Avg cost/run (USD)':<28} ${b['avg_cost_usd']:>11.6f} ${g['avg_cost_usd']:>11.6f} {imp['cost_reduction_pct']:>+9.1f}%")
    print(f"{'Avg tokens':<28} {b['avg_tokens']:>12.0f} {g['avg_tokens']:>12.0f} {imp['token_reduction_pct']:>+9.1f}%")
    print(f"{'Total duplicate calls':<28} {b['total_duplicates']:>12} {g['total_duplicates']:>12} {imp['duplicates_blocked']:>+10}")
    print(f"{'Success rate':<28} {b['success_rate_pct']:>11.1f}% {g['success_rate_pct']:>11.1f}%")
    print(f"{'='*60}")

    if s.get("advisor"):
        adv = s["advisor"]
        presc = adv.get("prescriptions", [])
        total_savings = adv.get("total_estimated_savings_pct", 0)
        print(f"\nOptimizationAdvisor — {len(presc)} prescriptions, ~{total_savings:.1f}% combined savings")
        for i, p in enumerate(presc[:5], 1):
            print(f"  {i}. {p['title']}: ~{p['estimated_savings_pct']:.0f}% savings [{p['effort']} effort]")

    print(f"\nResults saved to: {s.get('results_dir', '?')}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RunCore benchmark runner")
    parser.add_argument("--provider", required=True, choices=["groq", "gemini", "ollama"],
                        help="LLM provider to use")
    parser.add_argument("--model", default=None,
                        help="Model name (default: provider default)")
    parser.add_argument("--suite", default="support",
                        choices=list(ALL_TASKS.keys()) + ["all"],
                        help="Benchmark suite to run")
    parser.add_argument("--runs", type=int, default=1,
                        help="Runs per task (more = more reliable stats)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-run output")
    args = parser.parse_args()

    if args.suite == "all":
        for suite in ALL_TASKS:
            print(f"\n{'#'*60}")
            print(f"# Running suite: {suite}")
            print(f"{'#'*60}")
            run_suite(suite, args.provider, args.model,
                      runs_per_task=args.runs, verbose=not args.quiet)
    else:
        run_suite(args.suite, args.provider, args.model,
                  runs_per_task=args.runs, verbose=not args.quiet)


if __name__ == "__main__":
    main()
