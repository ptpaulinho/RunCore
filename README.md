# RunCore

**AI Agent Runtime Optimization Engine**

[![CI](https://github.com/ptpaulinho/RunCore/actions/workflows/ci.yml/badge.svg)](https://github.com/ptpaulinho/RunCore/actions)
[![PyPI](https://img.shields.io/pypi/v/runcore)](https://pypi.org/project/runcore/)
[![Python](https://img.shields.io/pypi/pyversions/runcore)](https://pypi.org/project/runcore/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

RunCore measures, analyzes, and **automatically optimizes** any LLM-powered agent — regardless of provider, framework, or architecture.

It introduces the **ATIR v1** open standard for agent traces and the **Cost per Successful Task (CpST)** metric as the primary efficiency signal. The unique value: RunCore closes the full loop — **observe → measure → optimize** — in three lines of code.

```
pip install runcore
```

---

## Why RunCore

Every other observability tool for LLM agents stops at observation. RunCore goes further:

| Tool | Observes | Measures CpST | Blocks waste in runtime | Prescribes fixes |
|------|----------|---------------|------------------------|-----------------|
| LangSmith | ✓ | ✗ | ✗ | ✗ |
| Helicone | ✓ | ✗ | ✗ | ✗ |
| Datadog LLM | ✓ | ✗ | ✗ | ✗ |
| **RunCore** | ✓ | ✓ | ✓ | ✓ |

---

## 3-line integration

```python
import runcore

# Zero-code: patches Anthropic + OpenAI SDK automatically
runcore.auto_instrument()

with runcore.capture("my_agent", task="process order INV-1001") as cap:
    response = anthropic_client.messages.create(...)  # captured automatically

trace = cap.get_atir()
print(f"CpST: ${trace.aggregates.cost_per_successful_task:.5f}")
print(f"Duplicate tool calls: {trace.aggregates.duplicate_tool_calls}")
```

---

## Runtime guards — active optimization

Add `guards=GuardConfig()` to block waste *before* it happens:

```python
import runcore
from runcore import GuardConfig

with runcore.capture("my_agent", task="handle ticket", guards=GuardConfig()) as cap:
    # Duplicate tool calls are blocked automatically (DuplicateToolCallError)
    # Loop risk is monitored — raises LoopBreakError if threshold exceeded
    # Context is compressed before LLM calls when tokens > threshold
    result = agent.run(task)

report = cap.savings_report()
print(report.summary_line())
# → "RunCore saved: 5 dup calls blocked, 1200 tokens compressed → ~$0.00390 saved"
```

### Guard configuration

```python
GuardConfig(
    # Block exact-duplicate tool calls (same name + same args)
    dedup_enabled=True,
    dedup_scope="turn",           # "turn" resets each LLM turn, "session" = entire run

    # Stop agent when loop risk exceeds threshold
    loop_break_enabled=True,
    loop_break_threshold=0.40,    # 0–1; above 0.40 = critical loop detected
    loop_break_min_calls=4,       # minimum tool calls before guard activates

    # Auto-compress context when input tokens exceed threshold
    context_compression_enabled=True,
    context_compression_token_threshold=800,
)
```

---

## What RunCore does

| Problem | RunCore solution |
|---------|-----------------|
| Agent costs are opaque | Real token + cost accounting at span level |
| No cross-provider comparison | ATIR v1 — provider-agnostic trace standard |
| Duplicate tool calls waste money | Runtime dedup guard — blocked before execution |
| Growing context = growing cost | Auto context compression — 28% avg token reduction |
| Loop detection too late | Loop Breaker guard — stops runaway agents in real time |
| "Is my agent getting worse?" | CpST drift monitoring with Slack/webhook alerts |
| Which model is most efficient? | Provider leaderboard ranked by CpST |
| Where do I start optimizing? | OptimizationAdvisor — ranked prescriptions with $ savings |

---

## Core metrics

### Cost per Successful Task (CpST)

The primary efficiency metric — unifies cost, success, and quality into one number:

```
CpST = total_cost_usd / max(1, successful_tool_calls)
```

Lower is better. Comparable across providers and agent versions.

### Loop Risk Score (LRS)

Real-time detection of pathological execution patterns:

```
LRS = 0.35 × dup_ratio
    + 0.25 × error_ratio
    + 0.20 × cycle_ratio
    + 0.20 × cross_turn_ratio
```

Scores in [0, 1]. Above 0.20 → warning. Above 0.40 → critical (loop breaker fires).

---

## Features

### OptimizationAdvisor

Analyzes a batch of ATIR traces and produces ranked prescriptions with estimated savings:

```python
from runcore.advisor import OptimizationAdvisor

advisor = OptimizationAdvisor()
report = advisor.analyze(atir_traces)

for p in report.prescriptions:
    print(f"[{p.effort.value}] {p.title}: ~{p.estimated_savings_pct:.1f}% savings")

# [low]    Eliminate duplicate tool calls: ~18.3% savings
# [medium] Compress conversation context: ~12.1% savings
# [low]    Slim down tool schemas sent to LLM: ~7.4% savings
```

### Real benchmarking

```bash
# Run baseline + optimized benchmark
runcore benchmark tests/fixtures/support.json --runs 20

# Compare two configs head-to-head
runcore compare --config-a '{}' --config-b '{"enable_context_compression": false}'
```

### Multi-provider leaderboard

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

runcore compare-providers "Classify this review: great product!" --runs 5
```

```
═══════════════════════════════════════════════════════════════════════
  RunCore Provider Leaderboard  —  ranked by Cost per Successful Task
═══════════════════════════════════════════════════════════════════════
  Rank  Provider              Model                      CpST       Cost
─────────────────────────────────────────────────────────────────────
  1     Claude Haiku 4.5      claude-haiku-4-5-20251001  $0.00015   $0.00015  ◀ winner
  2     GPT-4o-mini           gpt-4o-mini                $0.00023   $0.00023
  3     Claude Sonnet 4.6     claude-sonnet-4-6          $0.00180   $0.00180
  4     GPT-4o                gpt-4o                     $0.00520   $0.00520
═══════════════════════════════════════════════════════════════════════
```

### Continuous monitoring

```bash
# Watch a trace directory for CpST drift — alerts to Slack
runcore watch --source .runcore/traces --slack https://hooks.slack.com/... --interval 60
```

### Web dashboard

```bash
runcore serve
# → http://localhost:8000
```

Live benchmark progress via SSE. OptimizationAdvisor panel shows after each run.

---

## ATIR v1 — Agent Trace Intermediate Representation

An open standard for AI agent execution traces. ATIR is to agents what OpenTelemetry is to distributed systems — a common format that decouples producers from consumers.

```python
# Import from any source
atir = runcore.atir.from_dict(json.loads(Path("trace.atir.json").read_text()))
atir = runcore.atir.from_openai_response(openai_response)
atir = runcore.atir.from_anthropic_response(anthropic_response)

# Inspect
print(atir.aggregates.cost_per_successful_task)
print(atir.aggregates.duplicate_tool_calls)
print(atir.aggregates.loop_risk_score)
```

Full spec: [ATIR_SPEC.md](ATIR_SPEC.md)

---

## CLI reference

```
runcore init                    Initialize .runcore/ in current directory
runcore profile                 Capture a single agent trace
runcore benchmark <fixture>     Run baseline + optimized benchmark
runcore compare-providers       Multi-provider CpST leaderboard
runcore watch                   Continuous CpST monitoring daemon
runcore serve                   Start web dashboard
runcore atir validate <file>    Validate an ATIR trace file
runcore atir show <file>        Show trace summary
runcore import <file>           Import trace from any format
runcore instrument <script.py>  Auto-instrument and run a Python script
```

---

## Ecosystem Adapters

RunCore integrates natively with the three leading agent frameworks. No changes to your existing code are required — wrap once, get full ATIR traces.

### LangGraph

```python
from runcore.sdk.adapters.langgraph import RunCoreLangGraphTracer

tracer = RunCoreLangGraphTracer(agent_name="my_graph", task="process order")
app = tracer.wrap(graph.compile())          # transparent proxy
result = app.invoke({"messages": [...]})    # all nodes recorded automatically

trace = tracer.get_atir()
print(f"CpST: ${trace.aggregates.cost_per_successful_task:.5f}")
```

### CrewAI

```python
from runcore.sdk.adapters.crewai import trace_crew

with trace_crew("support_crew", task="handle tickets") as tracer:
    result = crew.kickoff()

trace = tracer.get_atir()
```

### AutoGen

```python
from runcore.sdk.adapters.autogen import RunCoreAutoGenTracer

tracer = RunCoreAutoGenTracer(agent_name="autogen_agent", task="code review")
result = tracer.initiate_chat(user_proxy, assistant, message="Review this PR")

trace = tracer.get_atir()
```

### LangChain / LCEL

Two modes — **Tracer** (owns the Capture, like the other adapters) or **Callback** (attaches to an existing `runcore.capture()` context):

```python
from runcore.sdk.adapters.langchain import RunCoreLangChainTracer, trace_chain

# Option A — wrap any LCEL Runnable
tracer = RunCoreLangChainTracer(agent_name="qa_chain", task="answer question")
wrapped = tracer.wrap(chain)
result  = wrapped.invoke({"question": "..."})
trace   = tracer.get_atir()

# Option B — context manager
with trace_chain("support_chain", task="route ticket") as tracer:
    result = chain.invoke({"input": "..."}, config={"callbacks": [tracer.callback]})

trace = tracer.get_atir()
print(f"CpST: ${trace.aggregates.cost_per_successful_task:.5f}")

# Option C — attach to existing capture() context
import runcore
from runcore.sdk.adapters.langchain import RunCoreLangChainCallback

with runcore.capture("my_chain", framework="langchain") as tracer:
    chain = MyChain(callbacks=[RunCoreLangChainCallback()])
    result = chain.run("some task")
```

Supported events: `on_llm_start/end/error`, `on_tool_start/end/error`, `on_chain_start/end/error`.

Install LangChain support: `pip install runcore[langchain]`

All adapters support runtime guards:

```python
from runcore.sdk.guards import GuardConfig

guards = GuardConfig(dedup_scope="session", loop_break_threshold=0.8)
tracer = RunCoreLangGraphTracer(..., guards=guards)
```

---

## Architecture

```
runcore/
├── sdk/            3-line capture + auto_instrument() + GuardConfig runtime guards
├── atir/           ATIR v1 spec, bidirectional converters
├── advisor/        OptimizationAdvisor — 6 prescription types
├── monitor/        Continuous monitoring daemon + alerting
├── benchmark/      Baseline/optimized runner, CpST metrics, provider bench
├── agents/         Simulated + real LLM agents
├── context/        ContextCompiler — semantic deduplication
├── loops/          Loop Risk Score detector
├── replacement/    Tool→Python replacement detection
├── server/         FastAPI dashboard with SSE streaming
└── cli/            Typer CLI
```

---

## Testing

```bash
pip install -e ".[dev]"
PYTHONPATH=. pytest tests/ -q
# 238 passed  (79 new adapter tests: LangGraph + CrewAI + AutoGen + LangChain)
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).  
ATIR v1 specification: Apache 2.0 — free to implement in any language or framework.

---

*RunCore is developed by [Saber3D](https://saber3d.pt).*
