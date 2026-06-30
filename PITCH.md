# RunCore — The Cost-Control Runtime for AI Agents

*Cut what your AI agent wastes — automatically — and prove it didn't get dumber.*

---

## The Problem

- **LLM agents waste 20–60% of their spend** on duplicate tool calls, ever-growing context,
  and undetected loops — with zero quality benefit. Token prices fall, but agents call *more*,
  so bills keep climbing.
- **Nobody catches it before production.** Teams ship a prompt or model change, the agent quietly
  gets 2× more expensive or starts looping, and they find out on the invoice.
- **Existing tools only watch.** LangSmith, Helicone, Langfuse show you cost *after* the fact.
  None of them *act* — and none prove a change didn't make the agent dumber.

## The Solution

**RunCore is a runtime that sits around any agent and removes the waste as it happens** — then
proves, with a hard success check, that nothing broke.

- **Runtime Guards** intercept duplicate tool calls, compress bloated context, and break runaway
  loops *before* they hit the API. Measured: **up to 46% fewer tokens, success preserved**
  (Groq, support suite, llama-3.1-8b, 12 runs — 12/12 success held).
- **CI gate** — a GitHub Action fails the build when an agent regresses (more expensive or less
  reliable) before it ships. This is the wedge: a test that catches cost/quality regressions.
- **Provider/framework agnostic** — wrap OpenAI, Anthropic, Groq, local models; LangGraph, CrewAI,
  or raw SDK. One line: `with runcore.capture(..., guards=GuardConfig()):`.
- **Zero-code capture** — `runcore.auto_instrument()` patches the LLM SDK; no agent rewrite.
- **Closed loop** — RunCore derives an optimization profile from your *production* traces, so it
  improves without a controlled A/B setup.

## Why now

- Agent spend is the new cloud bill, and it's unmanaged. FinOps exists for cloud; nothing equivalent
  exists for agents.
- Everyone ships agents fast and breaks them silently. The pain is *regression in production*,
  which teams feel weekly.

## Why us / why hard to copy

- The unique combination: **open trace standard (ATIR) + guards that act in runtime + profile
  derived from production traces.** Observability vendors watch; we watch, act, and prove.
- ATIR as an open standard creates lock-in / network effects if adopted.
- The hard part is the **success-preservation guarantee** — cutting cost is easy if you don't care
  about breaking the agent. Proving you didn't is the moat.

## How it makes money

Open-core. SDK and local use are free (adoption + data). Teams pay for:

| Tier | Price | What they pay for |
|---|---|---|
| Free | 0 | SDK, local guards, 1 cert/mo, public leaderboard |
| Team | $99/mo | CI gate, hosted dashboard, regression alerts, savings history |
| Enterprise | $499+/mo | Unlimited, private comparisons, continuous monitoring, procurement reports, support |

Expansion: per-seat CI, savings-based pricing (% of proven savings), private model comparison.

## Traction to chase (next 90 days)

1. **10 customer interviews** with teams running agents in production (script attached).
2. **3 design partners** running the CI gate on a real repo.
3. A handful of public leaderboard entries from real agents (credibility).

## Proof points (today)

- Working runtime guards: **46% token reduction, 12/12 success preserved** (real Groq runs,
  llama-3.1-8b, support suite, 12 runs) via dedup + stale-context elision.
- Live platform (dashboard, SDK, CLI, leaderboard, certification).
- Open methodology (`RUNCORE_SCORE_SPEC.md`) and open trace format (`ATIR_SPEC.md`).

## The ask

Validate the wedge with 10 interviews → land 3 design partners on the CI gate → turn proven savings
into paid Team/Enterprise. The badge/score is marketing; **the runtime that saves money is the
business.**
