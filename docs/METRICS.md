# RunCore Metrics Reference

## Cost per Successful Task (CpST)

The primary efficiency metric for LLM agents.

```
CpST = total_cost_usd / max(1, successful_tool_calls)
```

**Why CpST matters:**
- Regular cost metrics ($/token, $/call) don't capture whether the agent actually did useful work
- An agent that calls 10 tools and succeeds on 2 has higher CpST than one that calls 3 and succeeds on 3
- CpST is comparable across providers, models, and versions — it's your north star metric

**How to read it:**
- CpST = $0.00060 → each successful task costs 0.06 cents
- CpST improvement from $0.00773 to $0.00060 = 92% reduction

**Access:**
```python
trace = cap.get_atir()
cpst = trace.aggregates.cost_per_successful_task
```

---

## Loop Risk Score (LRS)

Detects pathological execution patterns before they cost money.

```
LRS = 0.35 × duplicate_ratio
    + 0.25 × error_ratio
    + 0.20 × no_progress_cycle_ratio
    + 0.20 × cross_turn_repeat_ratio
```

**Components:**
| Component | Weight | Meaning |
|-----------|--------|---------|
| `duplicate_ratio` | 0.35 | duplicate_calls / total_tool_calls |
| `error_ratio` | 0.25 | failed_calls / total_tool_calls |
| `cycle_ratio` | 0.20 | calls in no-progress windows / total_calls |
| `cross_turn_ratio` | 0.20 | calls repeated across LLM turns / total_calls |

**Thresholds:**
- `LRS < 0.20` → normal
- `0.20 ≤ LRS < 0.40` → warning (check your agent)
- `LRS ≥ 0.40` → critical (loop breaker fires if enabled)

**Access:**
```python
from runcore.loops import LoopDetector
detector = LoopDetector()
lrs = detector.calculate_loop_risk_score(trace)
```

---

## Prescription Priority Score

How the OptimizationAdvisor ranks fixes.

```
priority = (estimated_savings_pct × confidence) / effort_factor

effort_factor = { "low": 1.0, "medium": 2.0, "high": 4.0 }
```

A prescription with 30% savings, 0.9 confidence, low effort scores: (30 × 0.9) / 1.0 = 27.0

The same savings at high effort scores: (30 × 0.9) / 4.0 = 6.75

---

## ATIR Aggregates

All metrics available on a completed trace:

```python
agg = trace.aggregates

# Cost
agg.total_cost_usd           # total USD spent
agg.cost_per_successful_task # CpST
agg.avg_cost_per_llm_call    # average per LLM call

# Tokens
agg.total_tokens             # input + output
agg.input_tokens
agg.output_tokens

# Calls
agg.llm_calls                # number of LLM API calls
agg.tool_calls               # number of tool executions
agg.successful_tool_calls    # tool calls where success=True
agg.duplicate_tool_calls     # detected duplicates

# Performance
agg.total_duration_ms        # end-to-end latency
agg.avg_llm_latency_ms       # average LLM call latency

# Quality
agg.loop_risk_score          # LRS [0, 1]
agg.success_rate             # successful / total tool calls [0, 1]
agg.avg_quality_score        # average quality_score across runs
```
