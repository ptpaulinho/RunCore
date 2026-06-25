# RunCore Integration Guide

Instrument your AI agent in 3 steps. No code changes to your agent logic required.

---

## Step 1 — Install

```bash
# Pick the provider you use (or all of them)
pip install runcore                  # core only (local benchmarks, no LLM calls)
pip install "runcore[groq]"          # + Groq (free tier)
pip install "runcore[gemini]"        # + Gemini (free tier)
pip install "runcore[ollama]"        # + Ollama (local models)
pip install "runcore[all]"           # everything
```

## Step 2 — Wrap your agent

```python
import runcore

with runcore.capture("my_agent", task="Refund invoice #1001") as cap:
    result = your_agent.run("Refund invoice #1001 for customer@example.com")

trace = cap.get_atir()
print(f"Tokens used:  {trace.aggregates.total_tokens}")
print(f"Cost:         ${trace.aggregates.total_cost_usd:.6f}")
print(f"Dupl. calls:  {trace.aggregates.duplicate_tool_calls}")
```

`capture()` is a context manager — it intercepts LLM calls and tool invocations transparently.

## Step 3 — Run a benchmark

```bash
# Run your agent suite against free models and measure savings
python -m benchmarks.run_benchmark run --provider groq --suite support

# Or from Python
from benchmarks.runner import run_suite
summary = run_suite("support", provider_name="groq", model="llama3-8b-8192", runs_per_task=5)
```

Open the HTML report:
```bash
open benchmarks/results/<run-id>/report.html
```

Or start the local dashboard:
```bash
pip install "runcore[all]"
runcore serve
# → http://localhost:8000
```

---

## Framework examples

### Raw agent (OpenAI / Anthropic)

```python
import runcore
import openai

client = openai.OpenAI()

with runcore.capture("my_agent", task="summarise document") as cap:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Summarise this document: ..."}]
    )

trace = cap.get_atir()
```

### LangChain

```python
import runcore
from langchain.agents import AgentExecutor

agent_executor = AgentExecutor(agent=agent, tools=tools)

with runcore.capture("langchain_agent", task="weather query") as cap:
    result = agent_executor.invoke({"input": "What is the weather in Lisbon?"})

trace = cap.get_atir()
```

### CrewAI

```python
import runcore
from crewai import Crew

crew = Crew(agents=[researcher, writer], tasks=[task1, task2])

with runcore.capture("my_crew", task="research and write") as cap:
    output = crew.kickoff()

trace = cap.get_atir()
```

### AutoGen

```python
import runcore
import autogen

with runcore.capture("autogen_agent", task="write function") as cap:
    user_proxy.initiate_chat(assistant, message="Write a Python function to sort a list.")

trace = cap.get_atir()
```

---

## Apply optimizations (optional)

Let RunCore reduce cost automatically during execution:

```python
import runcore
from runcore import GuardConfig

config = GuardConfig(
    dedup_enabled=True,        # block repeated identical tool calls
    loop_breaker_enabled=True, # stop runaway loops
    compress_context=True,     # trim redundant context before LLM calls
)

with runcore.capture("my_agent", task="process order", guards=config) as cap:
    result = your_agent.run(task)

trace = cap.get_atir()
print(f"Calls blocked: {trace.aggregates.duplicate_tool_calls}")
```

---

## Reading results

| Field | What it means |
|---|---|
| `total_tokens` | Total tokens sent + received across all LLM calls |
| `total_cost_usd` | Estimated cost based on published model pricing |
| `duplicate_tool_calls` | Identical tool invocations blocked by dedup guard |
| `cost_per_successful_task` | Cost normalised to tasks that completed successfully |

### Benchmark report — PASS / FAIL

A run is **PASS** when cost savings versus the unguarded baseline meet or exceed your target (default 25%).

```
Baseline   →  Optimized    Savings
$0.0082    →  $0.0059      28.0%   ✅ PASS
```

---

## CI integration

Add RunCore to your test pipeline so every PR shows cost impact:

```yaml
# .github/workflows/benchmark.yml
- name: Run AI cost benchmark
  env:
    GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
  run: |
    pip install "runcore[groq]"
    python -m benchmarks.run_benchmark run --provider groq --suite support
```

The CLI exits with code `1` if savings fall below target — blocks the merge automatically.

---

## Cloud (optional)

To persist results and share with your team, send traces to RunCore Cloud via the CLI:

```bash
# Export a trace and push to Cloud
runcore atir export --run-id <run-id> | runcore cloud push --api-key rc_your_key_here
```

Or ingest directly from Python after capturing:

```python
import runcore, httpx

with runcore.capture("my_agent", task="process order") as cap:
    result = your_agent.run(task)

trace = cap.get_atir()
httpx.post(
    "https://app.runcore.ai/ingest",
    json=trace.model_dump(),
    headers={"X-Api-Key": "rc_your_key_here"},
)
```

Results appear instantly at [app.runcore.ai](https://app.runcore.ai).

Get an API key at [runcore.ai/signup](https://runcore.ai/signup) — free tier included.

---

## Troubleshooting

**`GROQ_API_KEY not set`** — get a free key at [console.groq.com](https://console.groq.com)

**`GEMINI_API_KEY not set`** — get a free key at [aistudio.google.com](https://aistudio.google.com)

**Ollama not reachable** — run `ollama serve` and `ollama pull llama3` first

**Import error on Python 3.9** — RunCore requires Python ≥ 3.10

---

## Support

- Issues: [github.com/ptpaulinho/RunCore/issues](https://github.com/ptpaulinho/RunCore/issues)
- Email: ppereira@saber3d.pt
