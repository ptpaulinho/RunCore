"""OptimizationProfile — real per-run optimization parameters derived from baseline traces."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from runcore.core.models import AgentTrace, OptimizationConfig
from runcore.tools.registry import ToolSchema


@dataclass
class OptimizationProfile:
    """Parameters applied to each optimized agent run.

    Built once from the baseline trace batch; injected into each optimized
    agent instance so the savings are genuinely measured, not simulated.
    """
    # Signatures of tool calls that appeared as duplicates in ≥50% of baseline
    # traces.  These are skipped on the very first occurrence in optimized runs.
    global_skip_signatures: frozenset[str] = field(default_factory=frozenset)

    # Runtime dedup: skip any tool call whose signature was already seen
    # *within the current run*, regardless of baseline patterns.
    runtime_dedup: bool = True

    # Whether to apply ContextCompiler compression before each LLM call.
    compress_context: bool = True

    # Tokens saved per LLM call from compressed tool schemas.
    # Computed once from agent.tools; subtracted from prompt_tokens on every call.
    schema_token_savings_per_call: int = 0


def _call_signature(name: str, args: dict) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True)}"


def build_profile(
    baseline_traces: list[AgentTrace],
    agent_tools: list[ToolSchema],
    config: OptimizationConfig,
) -> OptimizationProfile:
    """Derive a real OptimizationProfile from the completed baseline traces.

    Steps
    -----
    1. Find duplicate tool-call signatures that appear more than once within
       individual traces.  If a signature is a duplicate in ≥50% of traces
       that contain it, mark it as a global skip (always skip the 2nd+ call).
    2. Measure real token savings from compressing agent tool schemas.
    3. Respect the flags in *config*.
    """
    # --- 1. Global skip signatures ---
    global_skip: set[str] = set()
    if config.enable_loop_detection and baseline_traces:
        # Count how many traces have each signature appearing >1 time
        sig_trace_count: dict[str, int] = {}   # traces where sig is a dup
        sig_total_count: dict[str, int] = {}   # traces where sig appears at all

        for trace in baseline_traces:
            seen_in_trace: dict[str, int] = {}
            for tc in trace.tool_calls:
                sig = _call_signature(tc.name, tc.arguments)
                seen_in_trace[sig] = seen_in_trace.get(sig, 0) + 1

            for sig, count in seen_in_trace.items():
                sig_total_count[sig] = sig_total_count.get(sig, 0) + 1
                if count > 1:
                    sig_trace_count[sig] = sig_trace_count.get(sig, 0) + 1

        n = len(baseline_traces)
        for sig, dup_traces in sig_trace_count.items():
            total = sig_total_count.get(sig, 1)
            if dup_traces / total >= 0.5:  # duplicate in ≥50% of traces that have it
                global_skip.add(sig)

    # --- 2. Schema token savings ---
    schema_savings = 0
    if config.enable_tool_ranking and agent_tools:
        try:
            from runcore.tools.compression import compress_schemas
            compressed = compress_schemas(agent_tools)
            original_tokens = sum(t.token_count for t in agent_tools)
            compressed_tokens = sum(t.token_count for t in compressed)
            schema_savings = max(0, original_tokens - compressed_tokens)
        except Exception:
            schema_savings = 0

    return OptimizationProfile(
        global_skip_signatures=frozenset(global_skip),
        runtime_dedup=config.enable_loop_detection,
        compress_context=config.enable_context_compression,
        schema_token_savings_per_call=schema_savings,
    )


def build_profile_from_atir(
    atir_traces: list,  # list[ATIRTrace] — typed loosely to avoid circular import
    config: OptimizationConfig | None = None,
) -> OptimizationProfile:
    """Derive an OptimizationProfile directly from ATIR traces.

    This is the **external trace path**: if you captured real LLM traces via
    ``runcore.capture()`` or imported them from OpenAI / Anthropic response JSON,
    you can derive an optimization profile without ever running a simulated agent.

    Steps are the same as ``build_profile()`` but operate on ATIRTrace span data
    rather than AgentTrace tool call data.
    """
    from runcore.core.models import OptimizationConfig as _Cfg

    if config is None:
        config = _Cfg()

    # Build equivalent AgentTrace-like structures from ATIR spans
    from runcore.atir.converter import atir_to_agent_trace
    agent_traces = [atir_to_agent_trace(t) for t in atir_traces]

    return build_profile(agent_traces, agent_tools=[], config=config)
