"""RunCore benchmark CLI entry point.

Usage:
    python -m benchmarks.run_benchmark --provider groq --suite support
    python -m benchmarks.run_benchmark --provider ollama --suite all --runs 3
    python -m benchmarks.run_benchmark --provider gemini --report-only ./benchmarks/results/20260617_...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.tasks import ALL_TASKS


def cmd_run(args):
    from benchmarks.runner import run_suite
    from benchmarks.reporter import save_report

    provider = args.provider
    if not provider:
        print("ERROR: --provider is required. Choose: groq | gemini | ollama")
        sys.exit(1)

    # Verify provider is available before burning time
    try:
        if provider == "groq":
            from runcore.providers.groq import GroqProvider
            p = GroqProvider()
        elif provider == "gemini":
            from runcore.providers.gemini import GeminiProvider
            p = GeminiProvider()
        elif provider == "ollama":
            from runcore.providers.ollama import OllamaProvider
            p = OllamaProvider(model=args.model or "llama3.2")
        else:
            print(f"ERROR: Unknown provider '{provider}'")
            sys.exit(1)

        if not p.is_available():
            _print_setup_guide(provider)
            sys.exit(1)
    except ImportError as e:
        print(f"ERROR: {e}")
        _print_install_guide(provider)
        sys.exit(1)

    suites = list(ALL_TASKS.keys()) if args.suite == "all" else [args.suite]

    all_summaries = []
    for suite in suites:
        print(f"\n{'#'*64}")
        print(f"#  {provider.upper()} / {suite.upper()} — {args.runs} run(s) per task")
        print(f"{'#'*64}")
        summary = run_suite(
            suite_name=suite,
            provider_name=provider,
            model=args.model,
            runs_per_task=args.runs,
            verbose=not args.quiet,
        )
        all_summaries.append(summary)

        report_path = save_report(summary)
        print(f"\nHTML report: {report_path}")

    return all_summaries


def cmd_report(args):
    from benchmarks.reporter import generate_from_dir
    path = generate_from_dir(args.dir)
    print(f"Report regenerated: {path}")


def _print_setup_guide(provider: str):
    guides = {
        "groq": (
            "GROQ_API_KEY not set.\n"
            "  1. Sign up at https://console.groq.com (free)\n"
            "  2. Create an API key\n"
            "  3. export GROQ_API_KEY=gsk_..."
        ),
        "gemini": (
            "GEMINI_API_KEY not set.\n"
            "  1. Sign up at https://aistudio.google.com (free)\n"
            "  2. Get API key\n"
            "  3. export GEMINI_API_KEY=AIza..."
        ),
        "ollama": (
            "Ollama not running at localhost:11434.\n"
            "  1. Install: https://ollama.ai\n"
            "  2. ollama pull llama3.2\n"
            "  3. ollama serve"
        ),
    }
    print(f"\nERROR: {guides.get(provider, 'Provider not available.')}")


def _print_install_guide(provider: str):
    pkgs = {
        "groq": "pip install 'runcore[groq]'  # or: pip install groq",
        "gemini": "pip install 'runcore[gemini]'  # or: pip install google-generativeai",
        "ollama": "pip install 'runcore[ollama]'  # or: pip install ollama",
    }
    print(f"\nMissing dependency. Install with:\n  {pkgs.get(provider, 'pip install runcore[all]')}")


def main():
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.run_benchmark",
        description="RunCore benchmark runner — real LLM calls, recorded ATIR traces",
    )
    sub = parser.add_subparsers(dest="cmd")

    # run subcommand
    run_p = sub.add_parser("run", help="Run benchmark suite")
    run_p.add_argument("--provider", required=True, choices=["groq", "gemini", "ollama"])
    run_p.add_argument("--model", default=None, help="Override model name")
    run_p.add_argument("--suite", default="support",
                       choices=list(ALL_TASKS.keys()) + ["all"])
    run_p.add_argument("--runs", type=int, default=1, help="Runs per task")
    run_p.add_argument("--quiet", action="store_true")

    # report subcommand
    rep_p = sub.add_parser("report", help="Regenerate HTML report from saved results")
    rep_p.add_argument("dir", help="Path to results directory")

    # Default: if no subcommand, treat all args as 'run'
    if len(sys.argv) > 1 and sys.argv[1] not in ("run", "report", "-h", "--help"):
        sys.argv.insert(1, "run")

    args = parser.parse_args()

    if args.cmd == "run" or args.cmd is None:
        if not hasattr(args, "provider"):
            parser.print_help()
            sys.exit(0)
        cmd_run(args)
    elif args.cmd == "report":
        cmd_report(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
