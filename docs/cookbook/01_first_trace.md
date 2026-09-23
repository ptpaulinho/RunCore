# Cookbook 1 — First trace in 60 seconds

This example works without any API keys. It uses RunCore's simulated agent.

```python
import runcore
import json

# Record a trace manually
with runcore.capture("support_agent", task="process refund for order-123") as cap:
    # Simulate what your agent would do
    cap.record_tool(
        name="lookup_order",
        arguments={"order_id": "order-123"},
        result={"status": "delivered", "amount": 49.99},
        success=True,
        duration_ms=120.0,
    )
    cap.record_llm(
        provider="anthropic",
        model="claude-haiku-20240307",
        input_tokens=312,
        output_tokens=87,
        cost_usd=0.000041,
        duration_ms=850.0,
    )
    cap.record_tool(
        name="issue_refund",
        arguments={"order_id": "order-123", "amount": 49.99},
        result={"refund_id": "ref-456", "status": "approved"},
        success=True,
        duration_ms=200.0,
    )
    cap.set_quality(0.95)

trace = cap.get_atir()
print(f"CpST:        ${trace.aggregates.cost_per_successful_task:.6f}")
print(f"Total cost:  ${trace.aggregates.total_cost_usd:.6f}")
print(f"LLM calls:   {trace.aggregates.llm_calls}")
print(f"Tool calls:  {trace.aggregates.tool_calls}")
print(f"Success:     {trace.success}")

# Save trace
with open("trace.json", "w") as f:
    json.dump(trace.model_dump(mode="json"), f, indent=2, default=str)
print("Saved to trace.json")
```

**Output:**
```
CpST:        $0.000021
Total cost:  $0.000041
LLM calls:   1
Tool calls:  2
Success:     True
Saved to trace.json
```
