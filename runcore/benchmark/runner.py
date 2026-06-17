"""Benchmark runner — baseline and real-optimized runs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from runcore.agents.base import BaseAgent
from runcore.core.models import AgentTrace, OptimizationConfig
from runcore.benchmark.profile import build_profile

_MAX_WORKERS = 8


class BenchmarkRunner:
    """Runs baseline and optimized agent benchmarks in parallel.

    The optimized run uses a real OptimizationProfile built from the baseline
    traces — no fake token multipliers.  Savings are genuinely measured.
    """

    def run_baseline(
        self,
        agent: BaseAgent,
        tasks: list[str],
        runs_per_task: int = 5,
    ) -> list[AgentTrace]:
        """Run the agent without any optimizations."""
        jobs = [(task, i) for task in tasks for i in range(runs_per_task)]
        agent_cls = type(agent)

        def _run(task: str, _idx: int) -> AgentTrace:
            return agent_cls().run(task)

        return self._run_parallel(jobs, _run)

    def run_optimized(
        self,
        agent: BaseAgent,
        tasks: list[str],
        config: OptimizationConfig,
        runs_per_task: int = 5,
        baseline_traces: list[AgentTrace] | None = None,
    ) -> list[AgentTrace]:
        """Run the agent with a real OptimizationProfile applied.

        If *baseline_traces* are provided the profile is derived from them
        (real loop patterns, real schema savings).  Otherwise a fresh set of
        baseline runs is executed first.
        """
        if not baseline_traces:
            baseline_traces = self.run_baseline(agent, tasks, runs_per_task=max(3, runs_per_task // 2))

        profile = build_profile(baseline_traces, agent.tools, config)

        jobs = [(task, i) for task in tasks for i in range(runs_per_task)]
        agent_cls = type(agent)

        def _run(task: str, _idx: int) -> AgentTrace:
            return agent_cls(optimization=profile).run(task)

        return self._run_parallel(jobs, _run)

    def _run_parallel(self, jobs: list[tuple], fn) -> list[AgentTrace]:
        if not jobs:
            return []
        results: list[AgentTrace | None] = [None] * len(jobs)
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(jobs))) as pool:
            futures = {pool.submit(fn, *job): idx for idx, job in enumerate(jobs)}
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
        return [r for r in results if r is not None]
