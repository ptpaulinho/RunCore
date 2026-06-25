# Why AI Agent Benchmarks Lie About Cost

*A RunCore position paper · 2026*

---

## The 88% that costs $50

Open any AI agent leaderboard today. SWE-Bench, GAIA, Terminal-Bench, τ-bench. They rank
agents by one thing: **did it complete the task?** A 88% pass rate looks like a triumph.

Here's what the number hides: that 88% might have cost **$50 per task** in inference — or
**$0.50**. The leaderboard treats them as identical. A model that brute-forces the answer with
forty redundant tool calls and a 200k-token context scores the same as one that does it cleanly
in three calls.

In a benchmark, that's a footnote. In production, it's the difference between a viable product
and a company that quietly bleeds its margin into an LLM provider's revenue.

**We measure whether agents are *smart*. We have no standard for whether they are *efficient*.**

---

## Efficiency is now the bottleneck, not capability

Two years ago the hard question was "can the agent do it at all?" That question is mostly
answered — frontier models clear most real tasks. The question that decides whether your AI
product survives contact with a CFO is different:

> *How much successful work does this agent deliver per dollar?*

Nobody can answer it with a number you can trust, compare, or put in a contract. Teams
eyeball cloud bills. Procurement asks vendors to "estimate" cost and gets a marketing deck.
There is no SOC 2 for efficiency, no credit score for agents.

That vacuum is the opportunity.

---

## What's actually wrong (and fixable)

Most agent spend isn't buying intelligence. It's buying waste:

- **Duplicate tool calls** — the agent looks up the same record four times in one session.
- **Bloated context** — the entire conversation history re-sent on every single LLM call.
- **Undetected loops** — the agent retries a failing tool until something times out.

Studies and our own benchmarks put this at **30–60% of LLM spend** with zero quality benefit.
It is invisible precisely because no metric is pointed at it. Observability tools show you
*what happened*; they don't give you a single number that says *how efficient you are* — and
they definitely don't certify it.

---

## The RunCore Score™ — a standard, not a dashboard

RunCore exists to make agent efficiency **measurable, comparable, and provable**:

- **One number, 0–100.** 40% cost reduction, 35% token reduction, 25% task success — built on
  **CpST (Cost per Successful Task)**, the metric that actually reflects what you pay for.
- **Open methodology.** Every weight and threshold is published. No black box. Challenge it,
  reproduce it, fork it. A standard that isn't transparent isn't a standard.
- **Tamper-evident.** Each certification is reproducible and SHA-256 fingerprinted. Two parties
  running the same benchmark get the same fingerprint.
- **Portable proof.** A "RunCore Certified — Grade A" badge for your README, your landing page,
  your RFP response. A public leaderboard that ranks agents by efficiency, not just capability.

We're not trying to replace SWE-Bench. We're adding the axis it leaves out. *Can it?* and
*at what cost?* are different questions. The second one is now the one that matters.

---

## Where this goes

The benchmarks that win are the ones buyers ask for by name. Today no buyer can ask "what's
your efficiency score?" because the score doesn't exist. We intend to make it exist — open,
credible, and the default question in every AI procurement conversation.

If you build agents, **certify yours** and claim your place on the leaderboard. If you buy
agents, **start asking for the score.**

→ Methodology: [`RUNCORE_SCORE_SPEC.md`](RUNCORE_SCORE_SPEC.md) · Leaderboard: `/leaderboard` ·
Get certified: `runcore certify --provider groq`
