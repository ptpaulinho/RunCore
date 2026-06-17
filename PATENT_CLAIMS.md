# RunCore — Patent Claims & Prior Art Analysis

**Prepared by:** RunCore  
**Date:** 2026-06-17  
**Status:** Draft — for review by patent attorney before filing  
**Jurisdiction:** US (provisional), EU (pending)

---

## Executive Summary

RunCore introduces six novel inventions in the domain of AI agent runtime optimization. Each claim is distinct from prior art in that it operates at the **agent execution layer** (not the model layer), is **provider-agnostic**, and produces **measurable, verifiable outcomes** rather than heuristic approximations.

---

## Claim 1 — Cost per Successful Task (CpST) Metric

### What it is

A composite efficiency metric for AI agents defined as:

```
CpST = total_cost_usd / max(1, successful_tool_calls)
```

where `successful_tool_calls` is the count of tool invocations that completed without error, weighted by a quality score derived from task-specific outcome verification (not self-reported by the model).

More precisely:

```
CpST_weighted = total_cost_usd / (success_rate × quality_score × max(1, tool_calls))
```

### Why it is novel

Existing metrics (token count, latency, cost-per-token) measure **resource consumption** in isolation. CpST is the first published metric that unifies:

1. **Economic cost** (actual USD spent on LLM inference)
2. **Task success** (did the agent accomplish the stated goal)
3. **Output quality** (measured by verifiable downstream signals, not model self-evaluation)

into a single dimensionless ratio that is directly comparable across providers, models, and agent frameworks.

### Prior art analysis

- **OpenAI Evals** (2023): measures accuracy on fixed benchmarks; does not incorporate cost or real-time task success.
- **LangSmith** (2023): tracks cost and latency; no success-weighted efficiency ratio.
- **HellaSwag / BIG-Bench**: static dataset benchmarks; no per-run cost signal.
- **HELM** (Stanford, 2022): multi-metric benchmark; no unified scalar, no agent tool-call layer.

**Gap:** No prior work defines a unified scalar metric combining USD cost, task success rate, and quality at the individual agent run level in a way that is comparable across providers.

### Claims

> **C1.1** A method for measuring the efficiency of an AI agent system comprising: recording the total monetary cost of LLM inference calls within a single agent execution trace; verifying task completion through outcome-based signals independent of model self-evaluation; computing a normalized efficiency ratio as the ratio of total cost to verified successful outcomes.

> **C1.2** The method of C1.1 wherein quality score is computed from domain-specific verifiable signals (e.g., presence of required tool calls, downstream function execution success) rather than language model outputs.

> **C1.3** A system for comparing AI agents across providers wherein comparison is performed using CpST as the primary ranking metric, enabling provider-agnostic head-to-head benchmarking.

---

## Claim 2 — Loop Risk Score (LRS)

### What it is

A real-time, four-signal weighted composite score quantifying the probability that an AI agent is stuck in an unproductive execution loop:

```
LRS = 0.35 × dup_ratio
    + 0.25 × error_ratio
    + 0.20 × cycle_ratio
    + 0.20 × cross_turn_ratio
```

Where:
- `dup_ratio` = fraction of tool calls that are exact duplicates (same name + same arguments)
- `error_ratio` = fraction of tool calls that failed and were retried with identical arguments
- `cycle_ratio` = fraction of calls covered by no-progress windows (N consecutive identical calls)
- `cross_turn_ratio` = fraction of calls that repeat a previous call with ≥2 intervening calls (agent "forgot")

### Why it is novel

Existing loop detection in agentic systems uses simple iteration counters (`max_iterations`) or per-tool call limits. RunCore's LRS:

1. Is **multi-dimensional** — four independent signals, each capturing a distinct failure mode
2. Is **continuous** — produces a real-valued score in [0, 1], enabling graceful degradation policies
3. Operates on the **trace layer** — computed post-hoc from captured tool call sequences, not requiring model-level modifications
4. Supports **configurable policies** — different actions can be triggered at different LRS thresholds

### Prior art analysis

- **AutoGen** (Microsoft, 2023): `max_turns` parameter; binary stop signal; no risk scoring.
- **LangChain AgentExecutor**: `max_iterations` limit; no analysis of why loops occur.
- **CrewAI**: no loop detection beyond iteration cap.
- **ReAct** (Yao et al., 2022): no loop detection; assumes convergence.

**Gap:** No prior work defines a multi-dimensional, weighted composite score for AI agent loop risk that distinguishes between duplicate calls, error retry loops, no-progress cycles, and cross-turn repetition.

### Claims

> **C2.1** A method for detecting pathological execution loops in AI agent systems comprising: computing a duplicate-call ratio from a sequence of tool invocations; computing an error-retry ratio; computing a no-progress cycle coverage ratio; computing a cross-turn repetition ratio; producing a weighted composite risk score from said ratios.

> **C2.2** The method of C2.1 wherein loop risk score thresholds trigger configurable policies including early termination, context reset, tool subset restriction, or human escalation.

> **C2.3** A system for real-time loop risk monitoring wherein LRS is computed incrementally as each tool call is recorded, enabling early intervention before agent execution completes.

---

## Claim 3 — OptimizationAdvisor: Automatic Prescription Generation

### What it is

A system that analyzes a batch of AI agent execution traces (in ATIR format) and automatically produces a ranked list of **Prescriptions** — actionable optimization recommendations each carrying:

- Estimated percentage cost savings (computed from first principles, not heuristics)
- Estimated absolute USD savings per run
- Confidence score [0, 1]
- Effort estimate (low / medium / high)
- Evidence bullets (specific measurements from the analyzed traces)
- Priority score = (savings% × confidence) / effort_factor

Six prescription types are defined:
1. **DedupToolCalls** — eliminate duplicate tool invocations
2. **ContextCompression** — summarize growing conversation context
3. **SchemaSlim** — remove rarely-used tool schemas from LLM prompts
4. **ReplacementCandidate** — replace LLM tool calls with deterministic Python code
5. **LoopBreak** — add iteration guards based on LRS
6. **CacheWarm** — enable provider-level prompt caching for stable system prompts

### Why it is novel

The OptimizationAdvisor is the first published system that:

1. Works on **any agent trace** regardless of provider or framework (via ATIR)
2. Produces **quantified savings estimates** with confidence intervals, not generic advice
3. Ranks prescriptions by a **priority score** that accounts for effort, not just raw savings
4. Closes a feedback loop: traces → advisor → optimized profile → new traces

### Prior art analysis

- **LangSmith** (2023): cost tracking; no prescription generation.
- **Helicone** (2023): token analytics; no optimization advice.
- **Weights & Biases Prompts**: experiment tracking; no automated optimization.
- **Vertex AI Monitoring**: latency alerts; no multi-dimensional prescription ranking.

**Gap:** No prior system analyzes multi-run agent traces and produces ranked, quantified, effort-weighted optimization prescriptions.

### Claims

> **C3.1** A method for automatically generating optimization prescriptions for AI agent systems comprising: ingesting a plurality of execution traces in a provider-agnostic format; analyzing said traces across multiple optimization dimensions; for each dimension, computing an estimated savings value and confidence score; ranking resulting prescriptions by a composite priority score accounting for implementation effort.

> **C3.2** The method of C3.1 wherein estimated savings for each prescription are derived from first-principles token cost models, not from empirical regression on historical data.

> **C3.3** A system implementing C3.1 wherein prescriptions are derived from traces captured from any LLM provider without requiring provider-specific instrumentation.

---

## Claim 4 — Agent Trace Intermediate Representation (ATIR v1)

### What it is

A versioned, provider-agnostic JSON schema for recording AI agent execution traces, analogous to LLVM IR for compilers. ATIR defines:

- **LLMSpan**: records one LLM inference call (provider, model, tokens, cost, stop_reason)
- **ToolSpan**: records one tool invocation (name, arguments, result_summary, success, latency)
- **ATIRAggregates**: pre-computed summary statistics including CpST and duplicate_tool_calls
- **ATIRTrace**: root document with versioning, provider/framework metadata, and span list

ATIR includes:
- Bidirectional converter with RunCore's internal `AgentTrace` format
- One-call importers from raw OpenAI and Anthropic API responses
- Polymorphic span deserialization via the `type` discriminator field
- Semantic versioning with forward-compatibility guarantee (readers MUST ignore unknown fields)

### Why it is novel

ATIR is the first published open standard for AI agent execution traces that:

1. Is **bidirectional** — can be produced from any provider's native format AND consumed by any analysis tool
2. Includes **cost accounting** at the span level, not just at the session level
3. Defines **aggregates as a first-class object**, including the novel CpST metric
4. Is explicitly designed as an **open standard** with an Apache 2.0 license and versioning protocol

### Prior art analysis

- **OpenTelemetry**: general distributed tracing; no LLM-specific span types; no cost fields.
- **LangSmith traces**: proprietary; no open schema; no cross-provider import.
- **Arize Phoenix**: open-source LLM observability; no cost-per-successful-task; no optimization feedback loop.
- **W3C Trace Context**: HTTP header propagation standard; no agent-level semantics.

**Gap:** No open, versioned, bidirectional standard exists for AI agent execution traces with built-in cost accounting and optimization feedback.

### Claims

> **C4.1** A data format for recording AI agent execution traces comprising: a versioned root document; typed span records for LLM inference calls and tool invocations; pre-computed aggregate statistics including a cost-per-successful-task value; metadata fields for provider and framework identification.

> **C4.2** The data format of C4.1 wherein LLM span records include provider-normalized cost fields computed from provider-specific token pricing.

> **C4.3** A bidirectional converter between the data format of C4.1 and at least two proprietary LLM provider response formats, enabling cross-provider trace analysis without provider-specific instrumentation.

---

## Claim 5 — Automatic OptimizationProfile Derivation from External Traces

### What it is

A method by which an `OptimizationProfile` — a set of concrete optimization parameters to apply at agent execution time — is **automatically derived** from a batch of previously captured ATIR traces, without requiring the agent to be re-run in a controlled baseline/optimized experimental setup.

The derived profile includes:
- `global_skip_signatures`: frozenset of tool call (name, args) signatures that appeared as duplicates in ≥50% of analyzed traces — these are skipped on first occurrence in optimized runs
- `runtime_dedup`: boolean flag enabling per-run duplicate suppression
- `compress_context`: boolean flag enabling ContextCompiler during LLM calls
- `schema_token_savings_per_call`: integer token reduction from removing rarely-used tool schemas

### Why it is novel

Prior optimization systems require either:
- Manual configuration by the developer, or
- A controlled A/B experiment with a baseline and optimized variant

RunCore's `build_profile_from_atir()` derives the profile **directly from production traces**, closing the feedback loop without any experimental setup. This means optimization can begin from day one of deployment.

### Claims

> **C5.1** A method for automatically deriving runtime optimization parameters for an AI agent system comprising: analyzing a plurality of captured execution traces to identify statistically frequent duplicate tool call signatures; identifying tool schemas absent from a threshold fraction of traces; computing context compression parameters from observed token growth patterns; encoding said parameters into a runtime optimization profile applicable to subsequent agent executions.

> **C5.2** The method of C5.1 wherein the optimization profile is derived from traces captured in production (not in a controlled experimental environment), enabling zero-setup optimization deployment.

---

## Claim 6 — Zero-Code LLM SDK Instrumentation via Thread-Local Context Stack

### What it is

A method for capturing complete AI agent execution traces with **zero modifications to existing agent code**, using:

1. A **thread-local context stack** that tracks which `Capture` context manager is active per thread
2. A **monkey-patch layer** that intercepts `anthropic.resources.messages.Messages.create` and `openai.resources.chat.completions.Completions.create` at the class level
3. Automatic routing of intercepted calls to the innermost active `Capture` on the same thread

Usage:

```python
import runcore
runcore.auto_instrument()                         # one-time global patch

with runcore.capture("my_agent") as c:
    response = anthropic_client.messages.create(...)  # zero-code capture
    
trace = c.get_atir()    # complete ATIR v1 trace
```

The thread-local design ensures that **concurrent agents** running on different threads each capture their own trace without cross-contamination.

### Why it is novel

Existing observability libraries (LangSmith, Helicone, Arize) require:
- Wrapping the client object explicitly, or
- Using a custom HTTP proxy, or
- Modifying agent code to call tracing APIs

RunCore's approach is the first to use a **thread-local context stack combined with class-level method patching** to achieve zero-code capture with correct concurrency semantics.

### Claims

> **C6.1** A method for capturing AI agent execution traces without modification to agent source code comprising: maintaining a per-thread stack of active capture contexts; intercepting LLM SDK API calls at the SDK class level; routing intercepted calls to the innermost capture context on the calling thread; recording span data including token counts, cost, and latency.

> **C6.2** The method of C6.1 wherein concurrent agent executions on separate threads each produce independent, non-interleaved execution traces without requiring explicit trace context propagation in agent code.

> **C6.3** The method of C6.1 wherein the interception is reversible — a single `uninstrument()` call restores all original SDK methods — enabling safe use in test environments.

---

## Filing Strategy

### Recommended approach

1. **File a US Provisional Patent** covering all 6 claims above within the next 90 days. Cost: ~$1,500–3,000 with a patent attorney. This establishes priority date.

2. **File PCT International Application** within 12 months of provisional, covering US + EU + JP. Cost: ~$5,000–10,000.

3. **Open-source ATIR v1** (already done) to establish prior art for competitors while retaining patent rights on the optimization system built on top of it.

### Claims most likely to be granted

Ranked by novelty and examinability:

1. **C2 (Loop Risk Score)** — most concrete, most measurable, clearest gap from prior art
2. **C3 (OptimizationAdvisor)** — novel combination of prescription types with quantified savings
3. **C1 (CpST)** — simple formula but novel in combining cost + success + quality
4. **C6 (Zero-code instrumentation)** — strong if thread-local context stack combination is novel
5. **C4 (ATIR)** — weaker as a format claim; stronger as a system claim
6. **C5 (Profile from traces)** — dependent on C3/C4; best filed as a dependent claim

### Prior art search recommended

Before filing, conduct a freedom-to-operate search on:
- US 20230385739 (OpenAI, "Training language models")
- US 20230334259 (Anthropic, "Constitutional AI")
- US 20240086680 (Microsoft, "Agent orchestration")
- Any Weights & Biases, DataRobot, or Scale AI patents on ML observability

---

*This document is a technical summary for attorney review. Claim language will be refined by patent counsel prior to filing.*
