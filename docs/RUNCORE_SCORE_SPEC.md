# RunCore Score™ — Open Methodology Specification

**Version:** v1
**Status:** Stable
**Last updated:** 2026-06

The RunCore Score™ is an open, reproducible measure of how efficiently an AI agent
uses LLM resources to complete real tasks. This document specifies exactly how the
score is computed, so that **anyone can reproduce, audit, or challenge a result**.

> A standard that isn't transparent isn't a standard. Everything below is implemented
> in [`benchmarks/certification.py`](../benchmarks/certification.py) — no hidden weighting.

---

## 1. What the score answers

> "For the same task, completed successfully, how much cheaper and leaner is this
> agent than its unoptimized baseline?"

It is **not** a model-quality benchmark (that's SWE-Bench, GAIA, etc.). Those measure
*can the agent do it*. The RunCore Score measures *at what cost* — the dimension that
mainstream benchmarks ignore. A SWE-Bench score of 88% costing $50/task is treated as
identical to one costing $0.50/task. The RunCore Score exists to close that gap.

---

## 2. The core metric — CpST

**CpST = Cost per Successful Task** = `total_cost_usd / number_of_successful_tasks`.

Cost of failed tasks still counts (you paid for them), but only successes divide it.
An agent that is cheap but fails often has a *worse* CpST than a slightly pricier one
that succeeds. CpST is the north-star because it is what a CFO actually pays.

---

## 3. The score formula

The overall score is a weighted sum of three dimensions, each scored 0–100:

```
RunCore Score = 0.40 × cost_dimension
              + 0.35 × token_dimension
              + 0.25 × success_dimension
```

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| **Cost savings** | 40% | % cost reduction, optimized vs baseline |
| **Token reduction** | 35% | % fewer tokens sent to the LLM |
| **Task success** | 25% | fraction of tasks completed correctly |

Weights are defined in `SCORE_WEIGHTS` (`certification.py:40`).

### 3.1 Improvement % (cost and tokens)

For each dimension, improvement is measured against the agent's **own baseline** — the
same agent, same tasks, run with RunCore optimizations disabled:

```
improvement_pct = (baseline - optimized) / baseline × 100
```

**Free providers ($0 cost).** Some providers (Groq, Gemini free tier, local Ollama) report a cost
of $0, which makes a cost-reduction percentage undefined. For these, the **cost dimension tracks the
token dimension** — cost is proportional to tokens consumed, and a free tier is simply a $0 price
multiplier on the same underlying resource. This keeps the score meaningful for free models without
inventing a fake dollar figure.

### 3.2 Dimension scoring curve

Improvement % is mapped to a 0–100 dimension score via `_dimension_score()`:

| Improvement vs target | Dimension score |
|-----------------------|-----------------|
| ≤ 0% (regression) | `max(0, 50 + improvement×2)` — regressions penalised |
| between 0 and target | linear `0 → 70` |
| at target | **70** |
| between target and 2× target | linear `70 → 100` |
| ≥ 2× target | **100** |

**Targets:**
- Cost: **25%** reduction (`COST_TARGET`)
- Tokens: **20%** reduction (`TOKEN_TARGET`)

Rationale: hitting the target is "good" (70), not "perfect". Reaching 2× the target is
exceptional (100). This stops trivial wins from maxing the score.

### 3.3 Success dimension

```
success_dimension = fraction_of_successful_tasks × 100
```

Success is determined by **ground-truth actions, not self-report**. A task counts as successful when
the agent **called every `expected_tool`** and produced a non-empty final answer — or, for tool-free
tasks, when the answer satisfies the task's `success_keywords`. Tool-calling is the deterministic
signal that the agent did the work; keyword phrasing varies across models and is treated as a
secondary check, so a model that completes the task but words its summary differently is not
penalised. Defined in `benchmarks/agents/base.py`.

---

## 4. Grades & certification threshold

| Score | Grade | Badge colour |
|-------|-------|--------------|
| ≥ 90 | **A+** | green `#22c55e` |
| ≥ 80 | **A** | green |
| ≥ 70 | **B+** | blue `#3b82f6` |
| ≥ 60 | **B** | blue |
| ≥ 50 | **C** | amber `#f59e0b` |
| < 50 | **F** | red `#ef4444` |

**Certified** = overall score **≥ 60** (grade B or better) **AND** task success rate **≥ 60%**.

The success gate is deliberate: efficiency is meaningless without correctness. An agent that
slashes cost but fails most tasks must never be "certified efficient" — a cheap wrong answer has a
worse cost-per-*successful*-task than an expensive right one. So a high cost/token score alone
cannot earn certification; the agent must also actually complete its tasks. Defined in
`RunCoreScore.certified` (`MIN_SUCCESS_FOR_CERT`).

---

## 5. Benchmark suites

Certification runs against deterministic tasks with **canned tool responses** (so the
tool layer is identical across runs) but **real LLM reasoning** (the model actually
decides what to call). Defined in [`benchmarks/tasks.py`](../benchmarks/tasks.py).

| Suite | Tasks | Stresses |
|-------|-------|----------|
| `support` | 3 | redundant lookups, re-verification loops |
| `research` | 2 | unbounded search loops |
| `coding` | 2 | repeated file re-reads |
| `analytics` | 1 | repeated dataset fetches / context bloat |
| `all` | 8 | everything above |

Each task is run **twice per repetition** — baseline (guards off) and optimized
(guards on). With `runs_per_task = 5` and `suite = all`, that's `8 × 5 × 2 = 80` LLM calls.

A task counts as **successful** when the agent calls at least the `expected_tools_called`
and its final answer contains the `success_keywords`.

---

## 6. Reproducibility & tamper-evidence

- **Seeded:** agents run with `random.seed(42)` so the same benchmark on any machine
  produces the same structural result.
- **Confidence interval:** the score reports a 95% CI across per-run scores, so a single
  lucky run can't inflate the headline number.
- **SHA-256 fingerprint:** each report embeds a fingerprint computed from
  `{overall, provider, model, n_runs, timestamp, dimensions}` (`certification.py:292`).
  Two parties running the same certification get the same fingerprint — a result can be
  independently verified and cannot be silently edited.

---

## 7. How to reproduce a result

```bash
# CLI — exits 0 if certified, 1 if not (CI-friendly)
runcore certify --provider groq --model llama-3.1-8b-instant --runs 5 --suite all

# or via the dashboard: Certification → Run Certification
```

The generated HTML report contains the score, dimension breakdown, CI band, and the
SHA-256 fingerprint. Re-running with the same provider/model/runs reproduces it.

---

## 8. Versioning policy

The methodology is versioned (`v1`). Any change to weights, targets, the scoring curve,
or the suites bumps the version. Reports record the spec version they were produced
under, so historical scores remain interpretable. Breaking changes are never applied
retroactively to existing certifications.

---

## 9. What the score deliberately does NOT do

- It does not rank model intelligence — use SWE-Bench/GAIA for that.
- It does not reward raw cheapness — a cheap agent that fails scores poorly via CpST.
- It does not use hidden or proprietary weighting — everything is in this document.

---

*RunCore Score™ is an open methodology. Implementation: `benchmarks/certification.py`.
Challenges, reproductions, and PRs to the methodology are welcome.*
