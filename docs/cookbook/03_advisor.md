# Cookbook 3 — OptimizationAdvisor

The Advisor analyzes a batch of traces and produces ranked prescriptions.
Each prescription has an estimated savings percentage, confidence, and effort level.

## Basic usage

```python
import runcore
from runcore.advisor import OptimizationAdvisor

# Collect several traces first
traces = []
for task in ["process order", "check status", "issue refund"]:
    with runcore.capture("support_agent", task=task) as cap:
        # ... agent code ...
        cap.record_tool("lookup_order", {"order_id": "123"}, {...}, True, 100.0)
        cap.record_tool("lookup_order", {"order_id": "123"}, {...}, True, 100.0)  # duplicate
        cap.record_llm("anthropic", "claude-haiku-20240307", 400, 120, 0.00005, 900.0)
    traces.append(cap.get_atir())

# Analyze
advisor = OptimizationAdvisor()
report = advisor.analyze(traces, agent_name="support_agent")

print(f"Traces analyzed: {report.traces_analyzed}")
print(f"Avg cost/run:    ${report.avg_cost_per_run:.5f}")
print(f"Est. savings:    {report.total_estimated_savings_pct():.1f}%")
print()
print(report.summary)
print()
for i, p in enumerate(report.prescriptions, 1):
    print(f"{i}. [{p.effort.upper()}] {p.title}")
    print(f"   ~{p.estimated_savings_pct:.0f}% savings · confidence {p.confidence*100:.0f}%")
    print(f"   {p.description[:100]}")
    for ev in p.evidence[:2]:
        print(f"   • {ev}")
    print()
```

## Prescription types

RunCore identifies 6 types of waste:

| Type | What it detects | Typical savings |
|------|----------------|----------------|
| `dedup_tool_calls` | Same tool called with same args | 20–40% |
| `context_compression` | Context growing without summarization | 10–20% |
| `schema_slim` | Unused tools sent in every prompt | 5–15% |
| `replacement_candidate` | LLM used for deterministic operations | 5–20% |
| `loop_break` | High LRS, no guard configured | 10–30% |
| `cache_warm` | Stable system prompt not cached | 5–15% |

## Export to JSON

```python
report_dict = report.to_dict()
import json
print(json.dumps(report_dict, indent=2))
# → Full report with all prescriptions, evidence, and savings estimates
```

## Via HTTP API

```bash
curl -X POST http://localhost:8000/advice \
  -H "Content-Type: application/json" \
  -d '{
    "traces": [<atir_trace_dict>, ...],
    "agent_name": "support_agent"
  }'
```
