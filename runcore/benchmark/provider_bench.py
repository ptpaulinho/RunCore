"""Multi-provider head-to-head benchmark.

Runs the same task(s) against multiple LLM providers/models and produces
a CpST leaderboard — the killer demo for investors and M&A conversations.

Usage::

    from runcore.benchmark.provider_bench import ProviderBench, ProviderConfig

    bench = ProviderBench(tasks=["Classify this review as positive or negative: great product!"])
    bench.add_provider(ProviderConfig(
        label="Claude Haiku",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key_env="ANTHROPIC_API_KEY",
    ))
    bench.add_provider(ProviderConfig(
        label="GPT-4o-mini",
        provider="openai",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
    ))
    results = bench.run(runs_per_task=5)
    bench.print_leaderboard(results)
"""
from __future__ import annotations

import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from runcore.atir.spec import ATIRTrace, LLMSpan, ToolSpan
from runcore.sdk.capture import Capture
from runcore.trace.cost import calculate_llm_cost


@dataclass
class ProviderConfig:
    label: str
    provider: str           # "anthropic" | "openai" | "google" | "mistral"
    model: str
    api_key_env: str        # env var name that holds the API key
    system_prompt: str = "You are a helpful assistant."
    temperature: float = 0.0
    max_tokens: int = 512
    extra_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    label: str
    provider: str
    model: str
    runs: int
    avg_cpst: float
    avg_cost: float
    avg_input_tokens: float
    avg_output_tokens: float
    avg_latency_ms: float
    success_rate: float
    traces: list[ATIRTrace] = field(default_factory=list)
    error: str | None = None

    @property
    def rank_score(self) -> float:
        """Lower CpST = better. If all failed, use infinity."""
        if self.success_rate == 0:
            return float("inf")
        return self.avg_cpst

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "runs": self.runs,
            "avg_cpst": round(self.avg_cpst, 6),
            "avg_cost": round(self.avg_cost, 6),
            "avg_input_tokens": round(self.avg_input_tokens, 1),
            "avg_output_tokens": round(self.avg_output_tokens, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "success_rate": round(self.success_rate, 4),
            "error": self.error,
        }


class ProviderBench:
    """Orchestrates multi-provider benchmarks.

    Each provider runs the same tasks, results are ranked by CpST.
    Providers without API keys are skipped gracefully.
    """

    def __init__(
        self,
        tasks: list[str],
        system_prompt: str = "You are a helpful assistant.",
    ) -> None:
        self.tasks = tasks
        self.system_prompt = system_prompt
        self._providers: list[ProviderConfig] = []

    def add_provider(self, config: ProviderConfig) -> "ProviderBench":
        self._providers.append(config)
        return self

    def run(self, runs_per_task: int = 5) -> list[ProviderResult]:
        """Run all providers and return results sorted by CpST (ascending)."""
        results = []
        for cfg in self._providers:
            api_key = os.environ.get(cfg.api_key_env)
            if not api_key:
                results.append(ProviderResult(
                    label=cfg.label, provider=cfg.provider, model=cfg.model,
                    runs=0, avg_cpst=0, avg_cost=0, avg_input_tokens=0,
                    avg_output_tokens=0, avg_latency_ms=0, success_rate=0,
                    error=f"API key not set: ${cfg.api_key_env}",
                ))
                continue

            traces = []
            errors = 0
            for task in self.tasks:
                for _ in range(runs_per_task):
                    trace, err = self._run_single(cfg, task, api_key)
                    if trace:
                        traces.append(trace)
                    if err:
                        errors += 1

            total_runs = len(self.tasks) * runs_per_task
            results.append(self._aggregate(cfg, traces, total_runs))

        results.sort(key=lambda r: r.rank_score)
        return results

    def print_leaderboard(self, results: list[ProviderResult]) -> None:
        print("\n" + "═" * 72)
        print("  RunCore Provider Leaderboard  —  ranked by Cost per Successful Task")
        print("═" * 72)
        header = f"  {'Rank':<5} {'Provider':<20} {'Model':<28} {'CpST':>10} {'Cost':>10} {'Latency':>9} {'OK%':>6}"
        print(header)
        print("─" * 72)
        for i, r in enumerate(results, 1):
            if r.error:
                print(f"  {i:<5} {r.label:<20} {'— skipped':28}  {r.error}")
                continue
            marker = " ◀ winner" if i == 1 else ""
            print(
                f"  {i:<5} {r.label:<20} {r.model:<28}"
                f" ${r.avg_cpst:>9.5f}"
                f" ${r.avg_cost:>9.5f}"
                f" {r.avg_latency_ms:>7.0f}ms"
                f" {r.success_rate*100:>5.0f}%"
                f"{marker}"
            )
        print("═" * 72 + "\n")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_single(
        self, cfg: ProviderConfig, task: str, api_key: str
    ) -> tuple[ATIRTrace | None, str | None]:
        cap = Capture(agent_name=cfg.label, task=task, framework=cfg.provider)
        with cap:
            try:
                if cfg.provider == "anthropic":
                    return self._call_anthropic(cap, cfg, task, api_key), None
                elif cfg.provider == "openai":
                    return self._call_openai(cap, cfg, task, api_key), None
                else:
                    return None, f"Unsupported provider: {cfg.provider}"
            except Exception as exc:
                cap.set_success(False)
                return cap.get_atir(), str(exc)

    def _call_anthropic(
        self, cap: Capture, cfg: ProviderConfig, task: str, api_key: str
    ) -> ATIRTrace:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        t0 = time.perf_counter()
        response = client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=cfg.system_prompt,
            messages=[{"role": "user", "content": task}],
            temperature=cfg.temperature,
            **cfg.extra_params,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        usage = response.usage
        cost = calculate_llm_cost(cfg.model, usage.input_tokens, usage.output_tokens)
        cap.record_llm(
            provider="anthropic",
            model=cfg.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
            duration_ms=elapsed,
            stop_reason=str(response.stop_reason),
            messages_count=1,
        )
        return cap.get_atir()

    def _call_openai(
        self, cap: Capture, cfg: ProviderConfig, task: str, api_key: str
    ) -> ATIRTrace:
        import openai
        client = openai.OpenAI(api_key=api_key)
        t0 = time.perf_counter()
        response = client.chat.completions.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            messages=[
                {"role": "system", "content": cfg.system_prompt},
                {"role": "user", "content": task},
            ],
            temperature=cfg.temperature,
            **cfg.extra_params,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        usage = response.usage
        cost = calculate_llm_cost(cfg.model, usage.prompt_tokens, usage.completion_tokens)
        cap.record_llm(
            provider="openai",
            model=cfg.model,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cost_usd=cost,
            duration_ms=elapsed,
            stop_reason=str(response.choices[0].finish_reason),
            messages_count=2,
        )
        return cap.get_atir()

    def _aggregate(
        self, cfg: ProviderConfig, traces: list[ATIRTrace], total_runs: int
    ) -> ProviderResult:
        if not traces:
            return ProviderResult(
                label=cfg.label, provider=cfg.provider, model=cfg.model,
                runs=0, avg_cpst=0, avg_cost=0, avg_input_tokens=0,
                avg_output_tokens=0, avg_latency_ms=0, success_rate=0,
            )

        cpsts, costs, in_tok, out_tok, latencies = [], [], [], [], []
        successes = []

        for t in traces:
            agg = t.aggregates
            if agg:
                cpsts.append(agg.cost_per_successful_task)
                costs.append(agg.total_cost_usd)
                in_tok.append(agg.input_tokens)
                out_tok.append(agg.output_tokens)
                latencies.append(agg.total_duration_ms)
            successes.append(1.0 if t.success else 0.0)

        return ProviderResult(
            label=cfg.label,
            provider=cfg.provider,
            model=cfg.model,
            runs=len(traces),
            avg_cpst=statistics.mean(cpsts) if cpsts else 0.0,
            avg_cost=statistics.mean(costs) if costs else 0.0,
            avg_input_tokens=statistics.mean(in_tok) if in_tok else 0.0,
            avg_output_tokens=statistics.mean(out_tok) if out_tok else 0.0,
            avg_latency_ms=statistics.mean(latencies) if latencies else 0.0,
            success_rate=statistics.mean(successes) if successes else 0.0,
            traces=traces,
        )
