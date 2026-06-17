# RunCore — The Missing Layer for Production AI Agents

---

## The Problem

- **AI agents burn money in silence.** Duplicate tool calls, bloated context windows, and
  undetected loops consume 30–60% of LLM spend with no quality benefit.
- **There is no standard unit of agent efficiency.** Teams cannot compare models, frameworks, or
  prompt changes against a common baseline — so optimization is guesswork.
- **Observability tools tell you what happened. They do not fix it.** Tracing and logging
  produce dashboards; they do not block waste before it reaches the API.

---

## The Solution

- **Runtime Guards** intercept duplicate tool calls, compress bloated context, and break
  infinite retry loops *before* the LLM call is made — zero code changes to your agent logic.
- **CpST (Cost per Successful Task)** is a single, provider-agnostic KPI that collapses token
  count, cost, and success rate into one number your engineering and finance teams can track.
- **OptimizationAdvisor** reads ATIR traces and emits ranked, effort-scored prescriptions with
  estimated dollar savings — actionable in one sprint, not a quarter.

---

## Key Metrics

Numbers from the included `examples/demo_runcore.py` (simulated support agent, 5 tasks):

| Metric                          | Without RunCore | With RunCore | Delta   |
|---------------------------------|-----------------|--------------|---------|
| CpST (Cost per Successful Task) | $0.00773        | $0.00060     | **−92%**|
| Avg Cost / Run                  | $0.00773        | $0.00060     | −92%    |
| Avg Tokens / Run                | 2,402           | 2,020        | −16%    |
| Success Rate                    | 100%            | 100%         | 0%      |
| Duplicate Calls Blocked         | —               | 10           | —       |

OptimizationAdvisor identified 5 prescriptions with a combined estimated savings of **56%**
($0.00585/run) — all rated *low effort*.

---

## The Stack

| Component             | What it does                                                        |
|-----------------------|---------------------------------------------------------------------|
| **ATIR**              | Agent Trace Interchange Record — provider-neutral trace format (v1) |
| **CpST**              | Cost per Successful Task — the efficiency KPI for AI agents         |
| **OptimizationAdvisor** | Analyzes ATIR traces; emits ranked, confidence-scored prescriptions |
| **Guards**            | Runtime interception: dedup, loop-break, context compression        |
| **Adapters**          | Drop-in converters from OpenAI and Anthropic response objects       |

---

## Integrations

- **LangGraph** — instrument any StateGraph node with `@runcore.instrument`; ATIR exported per run.
- **CrewAI** — wrap crew execution in `runcore.capture()`; guards apply across all agent tool calls.
- **AutoGen** — monkey-patch the `ConversableAgent.initiate_chat` method via `auto_instrument()`.
- **LangChain** — `instrument_object(chain)` wraps any Runnable; zero prompt changes required.

---

## Business Model

| Tier           | Who it's for                        | Price                    |
|----------------|-------------------------------------|--------------------------|
| **OSS**        | Individual developers, research     | Free, MIT license        |
| **Team**       | Startups, ≤50 agents in production  | $499/mo — hosted dashboard + alerts |
| **Enterprise** | Large orgs, SLA, SSO, audit logs    | Custom — per-seat or usage-based   |

---

## Why Now

LLM API spend is doubling every 12 months as organizations move from demos to production
deployments. The shift from single-shot prompts to multi-step agents dramatically amplifies
waste: a 10-call agent with a 40% duplicate rate spends $0.40 of every $1.00 on nothing.
Framework fragmentation (LangGraph, CrewAI, AutoGen, custom) means no single vendor owns
the observability layer — RunCore is framework-neutral by design.

The window to establish a standard trace format (ATIR) and efficiency metric (CpST) is open
now, before hyperscalers bundle competing solutions.

---

## Contact

**Saber Porto** — ppereira@saber3d.pt  
Repo: `/Users/saberporto198/RunCore`  
Demo: `python3 examples/demo_runcore.py`
