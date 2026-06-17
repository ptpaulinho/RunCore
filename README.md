# RunCore

**AI Agent Runtime Optimization Engine**

[![CI](https://github.com/ptpaulinho/RunCore/actions/workflows/ci.yml/badge.svg)](https://github.com/ptpaulinho/RunCore/actions)
[![PyPI](https://img.shields.io/pypi/v/runcore)](https://pypi.org/project/runcore/)
[![Python](https://img.shields.io/pypi/pyversions/runcore)](https://pypi.org/project/runcore/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

> **Observe, measure, and automatically optimize any LLM-powered agent — in 3 lines of code.**

RunCore is the first tool that closes the full loop for AI agents: it doesn't just tell you what happened — it blocks waste before it reaches the API, prescribes ranked fixes with estimated savings, and gives you one number to track: **CpST** (Cost per Successful Task).

```
pip install runcore
```

---

## What problem does RunCore solve?

AI agents running in production routinely waste **30–60% of LLM spend** on three patterns that no observability tool currently stops:

| Pattern | Example | Typical waste |
|---------|---------|--------------|
| **Duplicate tool calls** | Agent calls `search("invoice 1001")` 4× in one session | 25–40% |
| **Bloated context** | Full conversation history sent to every LLM call | 15–25% |
| **Infinite loops** | Agent retries the same failing tool without a guard | 10–30% |

Every existing tool (LangSmith, Helicone, Datadog) **observes** these patterns after the fact. RunCore **blocks them in real time** and tells you exactly how much you saved.

---

## The RunCore difference

| Capability | LangSmith | Helicone | Datadog LLM | **RunCore** |
|-----------|-----------|----------|-------------|-------------|
| Observability (what happened) | ✓ | ✓ | ✓ | ✓ |
| CpST — unified efficiency metric | ✗ | ✗ | ✗ | ✓ |
| Blocks waste at runtime | ✗ | ✗ | ✗ | ✓ |
| Prescribes fixes with estimated $savings | ✗ | ✗ | ✗ | ✓ |
| Works across all frameworks | ✓ | ✓ | ✓ | ✓ |
| Open standard trace format (ATIR) | ✗ | ✗ | ✗ | ✓ |

---

## Quickstart — 3 lines of code

```python
import runcore

# Zero-code: patches Anthropic + OpenAI SDKs automatically
runcore.auto_instrument()

with runcore.capture("my_agent", task="process order INV-1001") as cap:
    # Your existing agent code — unchanged
    response = anthropic_client.messages.create(
        model="claude-haiku-20240307",
        max_tokens=1024,
        tools=[...],
        messages=[{"role": "user", "content": "Process order INV-1001"}],
    )

trace = cap.get_atir()
print(f"CpST: ${trace.aggregates.cost_per_successful_task:.5f}")
print(f"LLM calls: {trace.aggregates.llm_calls}")
print(f"Tool calls: {trace.aggregates.tool_calls}")
print(f"Total cost: ${trace.aggregates.total_cost_usd:.5f}")
```

---

## Runtime guards — block waste before it costs you

Add `guards=GuardConfig()` to activate three runtime protections:

```python
from runcore import GuardConfig

with runcore.capture("my_agent", guards=GuardConfig()) as cap:
    # DuplicateToolCallError raised if agent tries to call same tool twice
    # LoopBreakError raised if Loop Risk Score > 0.40
    # Context auto-compressed when messages exceed 800 tokens
    ...

report = cap.savings_report()
print(report.summary_line())
# → "Saved $0.0042: 8 duplicate calls blocked, 312 tokens compressed"
```

**GuardConfig options:**

```python
GuardConfig(
    dedup_enabled=True,          # block duplicate tool calls
    dedup_scope="turn",          # "turn" | "session"
    loop_break_enabled=True,     # break on LRS > threshold
    loop_break_threshold=0.40,   # LRS threshold (0–1)
    context_compression_enabled=True,  # compress context automatically
    token_threshold=800,         # compress when messages exceed N tokens
)
```

---

## OptimizationAdvisor — ranked prescriptions with estimated savings

```python
from runcore.advisor import OptimizationAdvisor

advisor = OptimizationAdvisor()
report = advisor.analyze(traces, agent_name="support_agent")

print(f"Combined estimated savings: {report.total_estimated_savings_pct():.1f}%")
for p in report.prescriptions:
    print(f"  {p.title}: ~{p.estimated_savings_pct:.0f}% savings, {p.effort} effort")
```

Output:
```
Combined estimated savings: 56.2%
  Eliminate duplicate tool calls: ~35% savings, low effort
  Compress growing context: ~18% savings, low effort
  Cache stable system prompt: ~12% savings, low effort
  Replace 2 tools with Python: ~8% savings, medium effort
  Add loop breaker guard: ~6% savings, low effort
```

---

## Framework adapters

Works with any agent framework — zero code changes to your agent:

### LangGraph

```python
from runcore.sdk.adapters import RunCoreLangGraphTracer

tracer = RunCoreLangGraphTracer("my_graph", task="process order")
app = tracer.wrap(graph.compile())          # transparent proxy

result = app.invoke({"messages": [...]})    # all nodes recorded
trace = tracer.get_atir()
print(tracer.savings_report())
```

### CrewAI

```python
from runcore.sdk.adapters import trace_crew

with trace_crew("support_crew", task="handle ticket #1234") as tracer:
    result = crew.kickoff()

trace = tracer.get_atir()
```

### AutoGen

```python
from runcore.sdk.adapters import RunCoreAutoGenTracer

tracer = RunCoreAutoGenTracer("code_reviewer", task="review PR #42")
result = tracer.initiate_chat(user_proxy, assistant, message="Review this PR")
trace = tracer.get_atir()
```

### LangChain / LCEL

```python
from runcore.sdk.adapters import RunCoreLangChainTracer

tracer = RunCoreLangChainTracer("qa_chain", task="answer question")
wrapped = tracer.wrap(chain)               # inject callback automatically

result = wrapped.invoke({"question": "..."})
trace = tracer.get_atir()
```

---

## Cloud auto-push — one line

After creating a tenant at your RunCore Cloud instance:

```python
import runcore

runcore.configure(
    api_key="rc_...",
    endpoint="https://your-runcore.onrender.com",
)

# Now every capture() automatically pushes the trace to Cloud
with runcore.capture("my_agent") as cap:
    ...
# → trace pushed in background, never blocks your code
```

---

## Metrics

### Cost per Successful Task (CpST)

The primary efficiency signal. Provider-agnostic, comparable across versions.

```
CpST = total_cost_usd / max(1, successful_tool_calls)
```

Lower is better. Track it over time to verify that changes actually improve efficiency — not just that they "look faster."

### Loop Risk Score (LRS)

```
LRS = 0.35 × duplicate_ratio
    + 0.25 × error_ratio
    + 0.20 × no_progress_cycle_ratio
    + 0.20 × cross_turn_repeat_ratio

LRS > 0.20 → warning
LRS > 0.40 → critical (loop breaker fires if enabled)
```

---

## ATIR — Agent Trace Intermediate Representation

ATIR v1 is an open standard for agent execution traces. Every RunCore trace is a valid ATIR document — portable, version-controlled, and importable from any source.

```python
# Export
trace = cap.get_atir()
with open("trace.json", "w") as f:
    json.dump(trace.model_dump(mode="json"), f)

# Import from any source
from runcore.atir import from_dict, from_anthropic_response, from_openai_response
trace = from_dict(json.load(open("trace.json")))
```

ATIR trace structure:
```json
{
  "atir_version": "1.0",
  "trace_id": "uuid",
  "agent_name": "support_agent",
  "task": "process order INV-1001",
  "started_at": "2026-06-17T10:00:00Z",
  "success": true,
  "quality_score": 0.95,
  "provider": "anthropic",
  "framework": "langchain",
  "spans": [
    {"type": "llm_call", "provider": "anthropic", "model": "claude-haiku-...",
     "input_tokens": 312, "output_tokens": 87, "cost_usd": 0.000041, ...},
    {"type": "tool_call", "name": "search_invoice", "success": true,
     "arguments": {"invoice_id": "INV-1001"}, ...}
  ],
  "aggregates": {
    "total_cost_usd": 0.000041,
    "total_tokens": 399,
    "llm_calls": 1,
    "tool_calls": 1,
    "cost_per_successful_task": 0.000041
  }
}
```

---

## CLI

```bash
# Start web dashboard
runcore serve

# Run benchmark (baseline vs optimized)
runcore benchmark tasks.json

# Compare providers by CpST
runcore compare-providers "Process a customer refund"

# Continuous monitoring daemon
runcore watch --source .runcore/traces/

# Inspect trace files
runcore atir show trace.json
runcore atir validate trace.json

# Import from OpenAI/Anthropic response
runcore import openai_response.json
```

---

## Web Dashboard

```bash
pip install runcore
runcore serve
# → http://localhost:8000
```

Features:
- Live benchmark progress (SSE streaming)
- Baseline vs optimized cost chart
- OptimizationAdvisor prescriptions panel
- Run history with filters

---

## Architecture

```
runcore/
├── sdk/           → capture(), auto_instrument(), GuardConfig
│   ├── adapters/  → LangGraph, CrewAI, AutoGen, LangChain
│   └── cloud.py   → configure(), push_trace()
├── atir/          → ATIRTrace, LLMSpan, ToolSpan, converters
├── advisor/       → OptimizationAdvisor, 6 prescription types
├── loops/         → LoopDetector, LRS formula
├── monitor/       → MonitorDaemon, alerts (Console/Webhook/Slack)
├── benchmark/     → BenchmarkRunner, BenchmarkComparison
├── context/       → ContextCompiler (semantic dedup + compression)
├── server/        → FastAPI dashboard + Cloud API + Billing
└── cli/           → Typer CLI (10+ commands)
```

---

## Installation

```bash
# Core
pip install runcore

# With Anthropic SDK
pip install "runcore[anthropic]"

# With OpenAI SDK
pip install "runcore[openai]"

# With LangChain
pip install "runcore[langchain]"

# Everything
pip install "runcore[all]"
```

---

## Cloud — hosted RunCore

Deploy your own RunCore Cloud instance (or use a shared one) for team-wide trace storage, dashboards, and billing:

- `POST /cloud/tenants` — create tenant, get API key
- `POST /cloud/ingest` — upload traces (Bearer API key)
- `GET /cloud/dashboard` — HTML dashboard with KPIs
- `GET /cloud/billing/plans` — Free / Team / Enterprise

Deploy in one click on [Render.com](https://render.com) using the included `render.yaml`.

---

## Benchmarks

Tested on a simulated support agent (5 tasks, 10 runs each):

| Metric | Baseline | With RunCore | Change |
|--------|---------|-------------|--------|
| CpST | $0.00773 | $0.00060 | **−92%** |
| Total tokens | 2,402 | 2,020 | −16% |
| Duplicate calls blocked | — | 10 | — |
| Loop risk score | 0.41 | 0.03 | −93% |

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

## Links

- [PyPI](https://pypi.org/project/runcore/)
- [ATIR Specification](ATIR_SPEC.md)
- [Changelog](CHANGELOG.md)
- [GitHub](https://github.com/ptpaulinho/RunCore)
