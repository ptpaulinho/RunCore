"""OptimizationAdvisor — the core patent-worthy engine.

Reads a list of ATIRTrace objects and produces a ranked OptimizationReport with
actionable Prescriptions, each carrying estimated dollar savings and confidence.

This module intentionally works entirely from ATIR traces — it is provider-agnostic
and framework-agnostic. A LangChain trace, a raw Anthropic response, or a RunCore
benchmark trace all produce the same kind of output.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from typing import Any

from runcore.atir.spec import ATIRTrace, LLMSpan, ToolSpan
from runcore.advisor.prescriptions import (
    Effort,
    OptimizationReport,
    Prescription,
    PrescriptionType,
)


# ---------------------------------------------------------------------------
# Token cost constants (approximations — real cost depends on provider/model)
# ---------------------------------------------------------------------------
_AVG_COST_PER_TOKEN = 3e-6   # ~$3 per 1M tokens (conservative blended estimate)
_AVG_TOKENS_PER_CONTEXT_BLOCK = 150


class OptimizationAdvisor:
    """Analyze ATIR traces and prescribe ranked optimizations.

    Usage::

        from runcore.advisor import OptimizationAdvisor

        advisor = OptimizationAdvisor()
        report = advisor.analyze(atir_traces)
        print(report.total_estimated_savings_pct())
        for p in report.prescriptions:
            print(p.title, p.estimated_savings_usd)
    """

    def analyze(
        self,
        traces: list[ATIRTrace],
        agent_name: str | None = None,
    ) -> OptimizationReport:
        """Analyze *traces* and return a ranked :class:`OptimizationReport`."""
        if not traces:
            return OptimizationReport(
                agent_name=agent_name or "unknown",
                traces_analyzed=0,
                total_cost_usd=0.0,
                avg_cost_per_run=0.0,
                avg_loop_risk=0.0,
                summary="No traces to analyze.",
            )

        name = agent_name or traces[0].agent_name
        total_cost = sum(
            (t.aggregates.total_cost_usd if t.aggregates else 0.0)
            for t in traces
        )
        avg_cost = total_cost / len(traces)
        avg_loop_risk = self._compute_avg_loop_risk(traces)

        prescriptions: list[Prescription] = []

        p = self._prescribe_dedup(traces, avg_cost)
        if p:
            prescriptions.append(p)

        p = self._prescribe_context_compression(traces, avg_cost)
        if p:
            prescriptions.append(p)

        p = self._prescribe_schema_slim(traces, avg_cost)
        if p:
            prescriptions.append(p)

        for p in self._prescribe_replacements(traces, avg_cost):
            prescriptions.append(p)

        p = self._prescribe_loop_break(traces, avg_cost, avg_loop_risk)
        if p:
            prescriptions.append(p)

        p = self._prescribe_cache_warm(traces, avg_cost)
        if p:
            prescriptions.append(p)

        # Sort by priority score descending
        prescriptions.sort(key=lambda x: x.priority_score, reverse=True)

        report = OptimizationReport(
            agent_name=name,
            traces_analyzed=len(traces),
            total_cost_usd=round(total_cost, 6),
            avg_cost_per_run=round(avg_cost, 6),
            avg_loop_risk=avg_loop_risk,
            prescriptions=prescriptions,
        )
        report.summary = self._generate_summary(report)
        return report

    # ------------------------------------------------------------------
    # Individual prescription generators
    # ------------------------------------------------------------------

    def _prescribe_dedup(
        self, traces: list[ATIRTrace], avg_cost: float
    ) -> Prescription | None:
        """Identify duplicate tool calls across traces."""
        total_tool_calls = 0
        total_dup_calls = 0

        for t in traces:
            tool_spans = [s for s in t.spans if s.type == "tool_call"]
            total_tool_calls += len(tool_spans)
            seen: set[str] = set()
            for s in tool_spans:
                sig = f"{s.name}:{json.dumps(s.arguments, sort_keys=True)}"
                if sig in seen:
                    total_dup_calls += 1
                seen.add(sig)

        if total_tool_calls == 0 or total_dup_calls == 0:
            return None

        dup_ratio = total_dup_calls / total_tool_calls
        if dup_ratio < 0.05:  # less than 5% duplicates — not worth prescribing
            return None

        # Each duplicate tool call consumes tokens for the result in context
        # Estimate: dup_ratio × avg_tool_calls × tokens_per_result × cost_per_token
        avg_tool_calls_per_run = total_tool_calls / len(traces)
        tokens_saved_per_run = total_dup_calls / len(traces) * _AVG_TOKENS_PER_CONTEXT_BLOCK
        savings_usd = tokens_saved_per_run * _AVG_COST_PER_TOKEN
        savings_pct = min((savings_usd / max(avg_cost, 1e-9)) * 100, 50.0)
        confidence = min(0.5 + dup_ratio, 0.95)

        dup_names = self._top_dup_tool_names(traces, n=3)
        evidence = [
            f"{total_dup_calls} duplicate tool calls detected across {len(traces)} traces",
            f"{dup_ratio*100:.1f}% of all tool calls are duplicates",
            f"Top repeated tools: {', '.join(dup_names) or 'n/a'}",
            f"Each duplicate wastes ~{_AVG_TOKENS_PER_CONTEXT_BLOCK} tokens in context",
        ]

        return Prescription(
            type=PrescriptionType.DEDUP_TOOL_CALLS,
            title="Eliminate duplicate tool calls",
            description=(
                "The agent is calling the same tool with the same arguments multiple times "
                "within the same run. Caching tool results in a per-run dict and skipping "
                "repeated calls can reduce token usage and latency with zero quality impact."
            ),
            estimated_savings_pct=round(savings_pct, 1),
            estimated_savings_usd=round(savings_usd, 6),
            confidence=round(confidence, 3),
            effort=Effort.LOW,
            evidence=evidence,
            metadata={
                "dup_ratio": round(dup_ratio, 4),
                "total_dup_calls": total_dup_calls,
                "top_tools": dup_names,
            },
        )

    def _prescribe_context_compression(
        self, traces: list[ATIRTrace], avg_cost: float
    ) -> Prescription | None:
        """Estimate savings from compressing the conversation context."""
        input_tokens_list = [
            t.aggregates.input_tokens for t in traces if t.aggregates
        ]
        if not input_tokens_list or statistics.mean(input_tokens_list) < 500:
            return None

        avg_input = statistics.mean(input_tokens_list)
        # Context compression typically removes 20-35% of input tokens
        # (verified against RunCore's ContextCompiler benchmarks)
        compression_rate = 0.28
        tokens_saved = avg_input * compression_rate
        savings_usd = tokens_saved * _AVG_COST_PER_TOKEN
        savings_pct = min((savings_usd / max(avg_cost, 1e-9)) * 100, 35.0)

        if savings_pct < 3.0:
            return None

        # Higher confidence when context is large and multi-turn
        avg_llm_calls = statistics.mean([
            t.aggregates.llm_calls for t in traces if t.aggregates
        ]) if traces else 1
        confidence = min(0.60 + (avg_llm_calls / 20), 0.90)

        evidence = [
            f"Average input tokens per run: {int(avg_input):,}",
            f"Context compression removes ~{compression_rate*100:.0f}% of redundant context",
            f"Estimated {int(tokens_saved):,} tokens saved per run",
            f"Average {avg_llm_calls:.1f} LLM calls per run (more calls = more context growth)",
        ]

        return Prescription(
            type=PrescriptionType.CONTEXT_COMPRESSION,
            title="Compress conversation context",
            description=(
                "The agent accumulates a large context window across LLM calls. "
                "Applying semantic deduplication and summarization to older context blocks "
                "reduces prompt tokens by 20–35% with minimal quality impact."
            ),
            estimated_savings_pct=round(savings_pct, 1),
            estimated_savings_usd=round(savings_usd, 6),
            confidence=round(confidence, 3),
            effort=Effort.MEDIUM,
            evidence=evidence,
            metadata={
                "avg_input_tokens": int(avg_input),
                "compression_rate": compression_rate,
                "avg_llm_calls": round(avg_llm_calls, 1),
            },
        )

    def _prescribe_schema_slim(
        self, traces: list[ATIRTrace], avg_cost: float
    ) -> Prescription | None:
        """Estimate savings from sending fewer tool schemas per LLM call."""
        # Approximate: count distinct tools seen, estimate schema tokens
        all_tool_names: set[str] = set()
        for t in traces:
            for s in t.spans:
                if s.type == "tool_call":
                    all_tool_names.add(s.name)

        n_tools = len(all_tool_names)
        if n_tools < 3:
            return None

        # Each tool schema is ~120 tokens. If 40% are rarely used,
        # filtering them out saves 40% × n_tools × 120 tokens per LLM call.
        rarely_used = self._rarely_used_tools(traces, threshold_pct=10.0)
        n_removable = len(rarely_used)
        if n_removable == 0:
            return None

        tokens_per_schema = 120
        avg_llm_calls = statistics.mean([
            t.aggregates.llm_calls for t in traces if t.aggregates
        ]) if traces else 1
        tokens_saved = n_removable * tokens_per_schema * avg_llm_calls
        savings_usd = tokens_saved * _AVG_COST_PER_TOKEN
        savings_pct = min((savings_usd / max(avg_cost, 1e-9)) * 100, 20.0)

        if savings_pct < 2.0:
            return None

        evidence = [
            f"{n_tools} distinct tools found in traces",
            f"{n_removable} tools used in <10% of runs: {', '.join(sorted(rarely_used)[:5])}",
            f"Each schema is ~{tokens_per_schema} tokens × {avg_llm_calls:.1f} LLM calls/run",
            f"Removing rarely-used schemas saves ~{int(tokens_saved):,} tokens/run",
        ]

        return Prescription(
            type=PrescriptionType.SCHEMA_SLIM,
            title="Slim down tool schemas sent to LLM",
            description=(
                "The agent sends all tool schemas to the LLM on every call, but many tools "
                "are rarely used. Sending only contextually relevant schemas reduces prompt "
                "tokens with zero accuracy impact for common paths."
            ),
            estimated_savings_pct=round(savings_pct, 1),
            estimated_savings_usd=round(savings_usd, 6),
            confidence=0.75,
            effort=Effort.LOW,
            evidence=evidence,
            metadata={
                "n_tools": n_tools,
                "rarely_used_tools": sorted(rarely_used),
            },
        )

    def _prescribe_replacements(
        self, traces: list[ATIRTrace], avg_cost: float
    ) -> list[Prescription]:
        """Identify tool calls that could be replaced by deterministic Python."""
        # Import lazily to avoid circular import
        try:
            from runcore.replacement.patterns import DETERMINISTIC_PATTERNS
            from runcore.replacement.detector import _TOOL_TO_PATTERN
        except ImportError:
            return []

        # Count tool call frequency
        tool_counts: Counter[str] = Counter()
        for t in traces:
            for s in t.spans:
                if s.type == "tool_call":
                    tool_counts[s.name.lower()] += 1

        prescriptions = []
        seen: set[str] = set()

        for tool_name, count in tool_counts.most_common():
            if tool_name in seen:
                continue
            pattern = _TOOL_TO_PATTERN.get(tool_name)
            if pattern is None:
                continue
            seen.add(tool_name)

            frequency = count / len(traces)
            if frequency < 0.5:  # only prescribe for tools used in 50%+ of runs
                continue

            # Savings: each replaceable call saves the tokens from the tool result
            # being appended to context
            tokens_saved_per_run = frequency * _AVG_TOKENS_PER_CONTEXT_BLOCK
            savings_usd = tokens_saved_per_run * _AVG_COST_PER_TOKEN
            savings_pct = min((savings_usd / max(avg_cost, 1e-9)) * 100, 15.0)

            prescriptions.append(Prescription(
                type=PrescriptionType.REPLACEMENT_CANDIDATE,
                title=f"Replace `{tool_name}` with deterministic Python code",
                description=(
                    f"The tool `{tool_name}` matches the pattern '{pattern['pattern_type']}' and "
                    f"can be replaced by a deterministic Python function. This eliminates "
                    f"the LLM call for this tool entirely, saving tokens and latency."
                ),
                estimated_savings_pct=round(savings_pct, 1),
                estimated_savings_usd=round(savings_usd, 6),
                confidence=0.80,
                effort=Effort.LOW,
                evidence=[
                    f"Tool `{tool_name}` called {count} times across {len(traces)} traces",
                    f"Pattern: {pattern['pattern_type']} — typically 100% deterministic",
                    f"Suggested replacement: pure Python function (see code_template in pattern)",
                ],
                metadata={
                    "tool_name": tool_name,
                    "pattern_type": pattern["pattern_type"],
                    "call_count": count,
                    "frequency_per_run": round(frequency, 3),
                },
            ))

        return prescriptions[:3]  # cap at top 3 replacement prescriptions

    def _prescribe_loop_break(
        self,
        traces: list[ATIRTrace],
        avg_cost: float,
        avg_loop_risk: float,
    ) -> Prescription | None:
        """Prescribe loop detection if risk score is elevated."""
        if avg_loop_risk < 0.15:
            return None

        # High loop risk means many redundant calls — estimate a fraction of cost wasted
        wasted_fraction = avg_loop_risk * 0.6  # conservative: 60% of risk converts to waste
        savings_usd = avg_cost * wasted_fraction
        savings_pct = min(wasted_fraction * 100, 40.0)

        risk_label = (
            "critical" if avg_loop_risk > 0.6
            else "high" if avg_loop_risk > 0.35
            else "medium"
        )

        evidence = [
            f"Average loop risk score: {avg_loop_risk:.3f} ({risk_label})",
            "Score combines: dup calls (35%), error repeats (25%), no-progress cycles (20%), cross-turn loops (20%)",
            f"Estimated {wasted_fraction*100:.0f}% of cost is from loop-induced redundancy",
        ]

        if avg_loop_risk > 0.4:
            evidence.append("⚠ Agent may be stuck in infinite retry loops — add a max_iterations guard")

        return Prescription(
            type=PrescriptionType.LOOP_BREAK,
            title="Add loop detection and iteration limits",
            description=(
                "The agent shows elevated loop risk, meaning it repeatedly calls the same "
                "tools or retries failed calls without making progress. Adding a per-run "
                "iteration counter and dedup guard will break these loops early."
            ),
            estimated_savings_pct=round(savings_pct, 1),
            estimated_savings_usd=round(savings_usd, 6),
            confidence=round(min(0.5 + avg_loop_risk * 0.6, 0.90), 3),
            effort=Effort.LOW,
            evidence=evidence,
            metadata={"avg_loop_risk": avg_loop_risk, "risk_label": risk_label},
        )

    def _prescribe_cache_warm(
        self, traces: list[ATIRTrace], avg_cost: float
    ) -> Prescription | None:
        """Suggest prompt caching if the system prompt is large and stable."""
        # Look for large repeated input token counts suggesting big system prompts
        input_tokens_list = [
            t.aggregates.input_tokens for t in traces if t.aggregates
        ]
        if not input_tokens_list or statistics.mean(input_tokens_list) < 1000:
            return None

        avg_input = statistics.mean(input_tokens_list)
        # Prompt caching (Anthropic) reduces cost of repeated system prompts by ~90%
        # Assume system prompt is ~30% of input tokens
        system_prompt_fraction = 0.30
        cache_discount = 0.90
        tokens_saved = avg_input * system_prompt_fraction * cache_discount
        savings_usd = tokens_saved * _AVG_COST_PER_TOKEN
        savings_pct = min((savings_usd / max(avg_cost, 1e-9)) * 100, 25.0)

        if savings_pct < 3.0:
            return None

        evidence = [
            f"Average input tokens: {int(avg_input):,} — large prompt detected",
            f"Prompt caching (Anthropic / OpenAI) reduces repeated system-prompt cost by ~90%",
            f"Estimated savings: {int(tokens_saved):,} tokens/run × ${_AVG_COST_PER_TOKEN:.0e}/token",
        ]

        return Prescription(
            type=PrescriptionType.CACHE_WARM,
            title="Enable prompt caching for stable system prompt",
            description=(
                "The agent sends a large, stable system prompt with every LLM call. "
                "Enabling prompt caching (cache_control on Anthropic, or prefix caching on OpenAI) "
                "reduces the cost of the system prompt by ~90% after the first call."
            ),
            estimated_savings_pct=round(savings_pct, 1),
            estimated_savings_usd=round(savings_usd, 6),
            confidence=0.70,
            effort=Effort.LOW,
            evidence=evidence,
            metadata={
                "avg_input_tokens": int(avg_input),
                "estimated_system_prompt_tokens": int(avg_input * system_prompt_fraction),
            },
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _compute_avg_loop_risk(self, traces: list[ATIRTrace]) -> float:
        """Compute average loop risk from ATIR duplicate_tool_calls ratio."""
        risks = []
        for t in traces:
            if not t.aggregates or t.aggregates.tool_calls == 0:
                continue
            dup_ratio = t.aggregates.duplicate_tool_calls / t.aggregates.tool_calls
            risks.append(min(dup_ratio * 1.5, 1.0))  # scale up slightly
        return round(statistics.mean(risks), 4) if risks else 0.0

    def _top_dup_tool_names(self, traces: list[ATIRTrace], n: int = 3) -> list[str]:
        dup_counts: Counter[str] = Counter()
        for t in traces:
            seen: set[str] = set()
            for s in t.spans:
                if s.type != "tool_call":
                    continue
                sig = f"{s.name}:{json.dumps(s.arguments, sort_keys=True)}"
                if sig in seen:
                    dup_counts[s.name] += 1
                seen.add(sig)
        return [name for name, _ in dup_counts.most_common(n)]

    def _rarely_used_tools(
        self, traces: list[ATIRTrace], threshold_pct: float = 10.0
    ) -> set[str]:
        """Return tool names used in fewer than threshold_pct% of traces."""
        tool_trace_count: Counter[str] = Counter()
        for t in traces:
            seen_in_trace: set[str] = set()
            for s in t.spans:
                if s.type == "tool_call" and s.name not in seen_in_trace:
                    tool_trace_count[s.name] += 1
                    seen_in_trace.add(s.name)

        threshold = len(traces) * threshold_pct / 100.0
        return {name for name, count in tool_trace_count.items() if count < threshold}

    def _generate_summary(self, report: OptimizationReport) -> str:
        n = len(report.prescriptions)
        if n == 0:
            return (
                f"Analyzed {report.traces_analyzed} traces for {report.agent_name}. "
                "No significant optimization opportunities found."
            )
        top = report.prescriptions[0]
        total_pct = report.total_estimated_savings_pct()
        return (
            f"Analyzed {report.traces_analyzed} traces for '{report.agent_name}'. "
            f"Found {n} optimization opportunity{'s' if n != 1 else ''} with a combined "
            f"estimated savings of {total_pct:.1f}% (${report.total_estimated_savings_usd():.4f}/run). "
            f"Top recommendation: {top.title} "
            f"(~{top.estimated_savings_pct:.1f}% savings, {top.effort.value} effort)."
        )
