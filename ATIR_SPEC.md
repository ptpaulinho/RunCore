# ATIR v1 — Agent Trace Intermediate Representation

**Version:** 1.0  
**Status:** Draft  
**License:** Apache 2.0 — free to implement in any language or framework  
**Maintainer:** RunCore (ppereira@saber3d.pt)

---

## Overview

ATIR is a provider-agnostic, versioned JSON format for AI agent execution traces. It is to agents what OpenTelemetry is to distributed systems — a common lingua franca that decouples trace producers (agent frameworks) from trace consumers (analytics, optimization, monitoring tools).

A single ATIR trace captures one complete agent run: every LLM call, every tool invocation, timing, token counts, cost, success, and quality — computed into standard aggregates.

---

## Design principles

1. **Provider-agnostic** — works with Anthropic, OpenAI, or any LLM provider
2. **Framework-agnostic** — works with LangChain, CrewAI, AutoGen, or custom agents
3. **Self-contained** — aggregates are computed and embedded; no external lookups needed
4. **Append-friendly** — spans are an ordered list; easy to stream or batch-append
5. **Versioned** — `atir_version` field ensures forward compatibility

---

## Top-level schema

```json
{
  "atir_version": "1.0",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "agent_name": "support_agent",
  "task": "Resolve ticket #4821 — password reset not working",
  "started_at": "2026-06-17T10:00:00Z",
  "finished_at": "2026-06-17T10:00:04.230Z",
  "success": true,
  "quality_score": 0.87,
  "provider": "anthropic",
  "framework": "runcore",
  "spans": [ "..." ],
  "aggregates": { "..." },
  "savings": null,
  "tags": ["production", "v2.1.0"],
  "metadata": {}
}
```

### Field reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `atir_version` | string | yes | Spec version. Currently `"1.0"` |
| `trace_id` | string (UUID) | yes | Unique identifier for this trace |
| `agent_name` | string | yes | Human-readable name for the agent |
| `task` | string | yes | Description of the task being performed |
| `started_at` | ISO 8601 datetime | yes | UTC timestamp when execution started |
| `finished_at` | ISO 8601 datetime | no | UTC timestamp when execution finished |
| `success` | boolean | yes | Whether the agent completed its task successfully |
| `quality_score` | float [0,1] | no | Optional quality score (0 = worst, 1 = best) |
| `provider` | string | yes | Primary LLM provider (`"anthropic"`, `"openai"`, `"unknown"`) |
| `framework` | string | yes | Agent framework (`"langchain"`, `"runcore"`, `"custom"`, etc.) |
| `spans` | array | yes | Ordered list of LLMSpan and ToolSpan objects |
| `aggregates` | ATIRAggregates | no | Computed summary. Populated by `finalize()` |
| `savings` | object | no | Runtime guard savings (populated when guards are active) |
| `tags` | array[string] | no | Free-form tags for filtering |
| `metadata` | object | no | Arbitrary key-value metadata |

---

## Span types

### LLMSpan

Represents a single call to an LLM API.

```json
{
  "type": "llm_call",
  "span_id": "a1b2c3d4-...",
  "provider": "anthropic",
  "model": "claude-haiku-4-5-20251001",
  "started_at": "2026-06-17T10:00:00.100Z",
  "duration_ms": 1240.5,
  "input_tokens": 1842,
  "output_tokens": 312,
  "cost_usd": 0.000285,
  "stop_reason": "end_turn",
  "messages_count": 6,
  "tools_count": 3,
  "metadata": {}
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"llm_call"` | yes | Discriminator literal |
| `span_id` | string (UUID) | yes | Unique span identifier |
| `provider` | string | yes | LLM provider name |
| `model` | string | yes | Model identifier as returned by the provider |
| `started_at` | ISO 8601 | yes | When this call was initiated |
| `duration_ms` | float | yes | Wall-clock duration in milliseconds |
| `input_tokens` | integer | yes | Tokens in the prompt/context |
| `output_tokens` | integer | yes | Tokens in the completion |
| `cost_usd` | float | yes | Cost in USD for this call |
| `stop_reason` | string | no | Why generation stopped (`"end_turn"`, `"max_tokens"`, `"tool_use"`, etc.) |
| `messages_count` | integer | no | Number of messages in the context |
| `tools_count` | integer | no | Number of tools available in this call |
| `metadata` | object | no | Provider-specific fields |

---

### ToolSpan

Represents a single tool (function) call made by the agent.

```json
{
  "type": "tool_call",
  "span_id": "b2c3d4e5-...",
  "name": "get_customer_record",
  "started_at": "2026-06-17T10:00:01.500Z",
  "duration_ms": 87.3,
  "input_tokens": 124,
  "success": true,
  "arguments": {
    "customer_id": "CUST-00142",
    "fields": ["email", "subscription_tier"]
  },
  "result_summary": "{\"email\": \"alice@example.com\", \"subscription_tier\": \"pro\"}",
  "error_message": null,
  "metadata": {}
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"tool_call"` | yes | Discriminator literal |
| `span_id` | string (UUID) | yes | Unique span identifier |
| `name` | string | yes | Tool/function name |
| `started_at` | ISO 8601 | yes | When this call was initiated |
| `duration_ms` | float | yes | Wall-clock duration in milliseconds |
| `input_tokens` | integer | yes | Estimated tokens consumed by this tool call |
| `success` | boolean | yes | Whether the tool call succeeded |
| `arguments` | object | yes | Arguments passed to the tool (must be JSON-serialisable) |
| `result_summary` | string | no | Truncated string representation of the result (max 200 chars) |
| `error_message` | string | no | Error message if `success=false` |
| `metadata` | object | no | Framework-specific fields |

---

## ATIRAggregates

Computed from all spans. Implementations should call `finalize()` after all spans are appended.

```json
{
  "total_tokens": 4821,
  "input_tokens": 3940,
  "output_tokens": 881,
  "total_cost_usd": 0.001842,
  "total_duration_ms": 4230.0,
  "llm_calls": 3,
  "tool_calls": 8,
  "successful_tool_calls": 7,
  "duplicate_tool_calls": 1,
  "cost_per_successful_task": 0.000263,
  "loop_risk_score": 0.12
}
```

| Field | Type | Description |
|-------|------|-------------|
| `total_tokens` | integer | `input_tokens + output_tokens` across all LLM spans |
| `input_tokens` | integer | Sum of input tokens across all LLM spans |
| `output_tokens` | integer | Sum of output tokens across all LLM spans |
| `total_cost_usd` | float | Sum of `cost_usd` across all LLM spans |
| `total_duration_ms` | float | Sum of `duration_ms` across all spans |
| `llm_calls` | integer | Count of LLMSpan objects |
| `tool_calls` | integer | Count of ToolSpan objects |
| `successful_tool_calls` | integer | Count of ToolSpan where `success=true` |
| `duplicate_tool_calls` | integer | Count of ToolSpan with identical `name + arguments` |
| `cost_per_successful_task` | float | **CpST** — `total_cost_usd / max(1, successful_tool_calls)` |
| `loop_risk_score` | float [0,1] | Composite loop risk. `null` if not computed |

---

## Core metrics

### Cost per Successful Task (CpST)

The primary efficiency metric for an agent run:

```
CpST = total_cost_usd / max(1, successful_tool_calls)
```

**Why this metric:**
- Pure cost metrics reward agents that do nothing
- Pure success metrics ignore cost
- CpST penalises both expensive runs and unsuccessful tool use
- Comparable across providers, models, and agent versions
- Lower is better

### Loop Risk Score (LRS)

A composite signal in [0, 1] detecting pathological execution patterns:

```
LRS = 0.35 × dup_ratio
    + 0.25 × error_ratio
    + 0.20 × cycle_ratio
    + 0.20 × cross_turn_ratio
```

Where:
- `dup_ratio` = `duplicate_tool_calls / max(1, tool_calls)`
- `error_ratio` = `(tool_calls - successful_tool_calls) / max(1, tool_calls)`
- `cycle_ratio` = detected cyclic call patterns / total calls
- `cross_turn_ratio` = identical calls repeated across LLM turns / total calls

Thresholds: `LRS > 0.20` → warning. `LRS > 0.40` → critical.

---

## Savings object

Populated when runtime guards are active. Records what was prevented during execution:

```json
{
  "blocked_tool_calls": 3,
  "tokens_saved": 650,
  "cost_saved_usd": 0.00195,
  "compression_runs": 1,
  "tokens_saved_compression": 200,
  "loop_breaks": 0
}
```

---

## Complete example

```json
{
  "atir_version": "1.0",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "agent_name": "support_agent",
  "task": "Resolve ticket #4821",
  "started_at": "2026-06-17T10:00:00.000Z",
  "finished_at": "2026-06-17T10:00:04.230Z",
  "success": true,
  "quality_score": 0.87,
  "provider": "anthropic",
  "framework": "runcore",
  "tags": ["production"],
  "metadata": {},
  "savings": null,
  "spans": [
    {
      "type": "llm_call",
      "span_id": "a1b2c3d4-0000-0000-0000-000000000001",
      "provider": "anthropic",
      "model": "claude-haiku-4-5-20251001",
      "started_at": "2026-06-17T10:00:00.100Z",
      "duration_ms": 1240.5,
      "input_tokens": 1842,
      "output_tokens": 312,
      "cost_usd": 0.000285,
      "stop_reason": "tool_use",
      "messages_count": 4,
      "tools_count": 3,
      "metadata": {}
    },
    {
      "type": "tool_call",
      "span_id": "b2c3d4e5-0000-0000-0000-000000000002",
      "name": "get_customer_record",
      "started_at": "2026-06-17T10:00:01.500Z",
      "duration_ms": 87.3,
      "input_tokens": 124,
      "success": true,
      "arguments": {"customer_id": "CUST-00142"},
      "result_summary": "{\"email\": \"alice@example.com\"}",
      "error_message": null,
      "metadata": {}
    }
  ],
  "aggregates": {
    "total_tokens": 2278,
    "input_tokens": 1966,
    "output_tokens": 312,
    "total_cost_usd": 0.000285,
    "total_duration_ms": 1327.8,
    "llm_calls": 1,
    "tool_calls": 1,
    "successful_tool_calls": 1,
    "duplicate_tool_calls": 0,
    "cost_per_successful_task": 0.000285,
    "loop_risk_score": 0.0
  }
}
```

---

## Implementation notes

### Producing ATIR traces

Any language or framework can produce ATIR traces. Requirements:
1. Generate a UUID v4 for `trace_id` and each `span_id`
2. Record timestamps in UTC ISO 8601 format
3. Call `finalize()` (or equivalent) after all spans are appended — this computes `aggregates`
4. Set `atir_version = "1.0"`

### Consuming ATIR traces

Consumers should:
1. Check `atir_version` and reject unknown major versions
2. Treat `aggregates` as authoritative — do not recompute from spans unless validating
3. Handle `null` values gracefully for all optional fields

### Versioning

ATIR follows semantic versioning:
- **Minor version bump** (1.0 → 1.1): additive fields, backwards compatible
- **Major version bump** (1.x → 2.0): breaking changes to required fields or metric formulas

---

## Implementations

| Language | Package | Status |
|----------|---------|--------|
| Python | `runcore` (`pip install runcore`) | Reference implementation |
| TypeScript | — | Planned |
| Go | — | Planned |

To add your implementation to this list, open a PR or contact ppereira@saber3d.pt.

---

*ATIR v1 is developed and maintained by [RunCore](https://github.com/runcore-ai/runcore).*  
*Licensed under Apache 2.0 — free to use, implement, and extend.*
