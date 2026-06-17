# Cookbook 2 — Runtime guards

Guards block waste before it reaches the LLM API.

## Dedup guard — block duplicate tool calls

```python
import runcore
from runcore import GuardConfig, DuplicateToolCallError

with runcore.capture("agent", guards=GuardConfig(dedup_enabled=True)) as cap:
    # First call — recorded normally
    cap.record_tool("search", {"q": "invoice 1001"}, {"result": "..."}, True, 100.0)

    # Second identical call — raises DuplicateToolCallError
    try:
        cap.record_tool("search", {"q": "invoice 1001"}, {"result": "..."}, True, 100.0)
    except DuplicateToolCallError as e:
        print(f"Blocked: {e}")
        # → "Blocked: Duplicate tool call blocked: search(q=invoice 1001)"

report = cap.savings_report()
print(report.summary_line())
# → "Saved $0.0003: 1 duplicate call blocked"
```

## Loop break guard — stop infinite loops

```python
from runcore import GuardConfig, LoopBreakError

with runcore.capture("agent", guards=GuardConfig(loop_break_enabled=True, loop_break_threshold=0.40)) as cap:
    # Simulate a looping agent
    for i in range(5):
        cap.record_tool("search", {"q": f"try_{i}"}, None, False, 50.0)

    # Check loop risk manually
    try:
        # Gets LRS from spans, raises if > threshold
        atir = cap.get_atir()
        lrs = atir.aggregates.loop_risk_score if atir.aggregates else 0
        cap.check_loop_risk(lrs)
    except LoopBreakError as e:
        print(f"Loop detected: {e}")
```

## Context compression guard

```python
from runcore import GuardConfig

config = GuardConfig(
    context_compression_enabled=True,
    token_threshold=800,   # compress when messages exceed 800 tokens
)

with runcore.capture("agent", guards=config) as cap:
    long_messages = [
        {"role": "user", "content": "..." * 100},
        {"role": "assistant", "content": "..." * 100},
        # ... many turns
    ]
    # Automatically compressed when over threshold
    compressed = cap.compress_messages(long_messages, estimated_tokens=1200)
    print(f"Original: {len(long_messages)} messages")
    print(f"Compressed: {len(compressed)} messages")
```

## All guards together

```python
from runcore import GuardConfig

guards = GuardConfig(
    dedup_enabled=True,
    dedup_scope="session",           # "turn" (default) or "session"
    loop_break_enabled=True,
    loop_break_threshold=0.35,       # more aggressive
    context_compression_enabled=True,
    token_threshold=600,
)

with runcore.capture("production_agent", task="...", guards=guards) as cap:
    ...

report = cap.savings_report()
print(f"Duplicate calls blocked: {report.blocked_tool_calls}")
print(f"Tokens saved (dedup):     {report.blocked_tool_calls_tokens}")
print(f"Tokens saved (compress):  {report.tokens_saved_compression}")
print(f"USD saved total:          ${report.blocked_tool_calls_cost_usd + report.cost_saved_compression_usd:.4f}")
```
