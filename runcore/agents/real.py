"""Real LLM agent — uses Anthropic SDK for genuine API calls."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from runcore.agents.base import BaseAgent
from runcore.core.models import AgentTrace
from runcore.trace.cost import calculate_llm_cost

# ---------------------------------------------------------------------------
# Simulated tool implementations (real LLM, fake data store)
# ---------------------------------------------------------------------------

_INVOICE_DB: dict[str, dict] = {
    "INV-1001": {"id": "INV-1001", "amount": 99.99, "status": "paid", "date": "2024-05-10", "customer_email": "john@example.com"},
    "INV-5042": {"id": "INV-5042", "amount": 149.00, "status": "shipped", "date": "2024-05-15", "customer_email": "jane@example.com"},
    "INV-3311": {"id": "INV-3311", "amount": 49.99, "status": "paid", "date": "2024-05-01", "customer_email": "bob@example.com"},
}

_CUSTOMER_DB: dict[str, dict] = {
    "john@example.com": {"id": "cust_42", "name": "John Doe", "email": "john@example.com", "tier": "premium"},
    "jane@example.com": {"id": "cust_17", "name": "Jane Smith", "email": "jane@example.com", "tier": "standard"},
    "bob@example.com": {"id": "cust_99", "name": "Bob Wilson", "email": "bob@example.com", "tier": "standard"},
}


def _execute_tool(name: str, args: dict[str, Any]) -> Any:
    """Execute a tool call and return its result."""
    if name == "get_invoice":
        invoice_id = args.get("invoice_id", "")
        return _INVOICE_DB.get(invoice_id) or {"error": f"Invoice {invoice_id} not found"}

    if name == "get_customer":
        email = args.get("email", "")
        return _CUSTOMER_DB.get(email) or {"error": f"Customer {email} not found"}

    if name == "search_docs":
        query = args.get("query", "")
        return {"results": [f"Doc: Refund policy — {query}", "Doc: Standard processing time is 3-5 business days"]}

    if name == "refund_order":
        order_id = args.get("order_id", "")
        amount = args.get("amount", 0)
        return {"status": "refunded", "order_id": order_id, "amount": amount, "ref": f"REF-{hash(order_id) % 9999:04d}", "eta_days": 3}

    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Tool schemas for the Anthropic API
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS = [
    {
        "name": "get_invoice",
        "description": "Retrieve an invoice record by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string", "description": "The invoice identifier, e.g. INV-1001"}},
            "required": ["invoice_id"],
        },
    },
    {
        "name": "get_customer",
        "description": "Retrieve a customer record by email address.",
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string", "description": "Customer email address"}},
            "required": ["email"],
        },
    },
    {
        "name": "search_docs",
        "description": "Search the support knowledge base for relevant documentation.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
    {
        "name": "refund_order",
        "description": "Process a refund for a given order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The order/invoice ID to refund"},
                "amount": {"type": "number", "description": "Amount to refund in USD"},
            },
            "required": ["order_id", "amount"],
        },
    },
]

_SYSTEM_PROMPT = (
    "You are a customer support agent for an e-commerce platform. "
    "Resolve customer issues efficiently using the available tools. "
    "Always look up the customer and invoice before processing a refund. "
    "Be concise and professional."
)


class RealSupportAgent(BaseAgent):
    """Support agent that makes real Anthropic API calls."""

    name = "real_support"
    model = "claude-haiku-4-5-20251001"

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__()
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def _get_client(self):
        try:
            import anthropic
        except ImportError as e:
            raise ImportError("anthropic package required: pip install anthropic") from e
        if not self._api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Export it or pass api_key= to RealSupportAgent()."
            )
        return __import__("anthropic").Anthropic(api_key=self._api_key)

    def run(self, task: str) -> AgentTrace:
        run_id = self.collector.start_run(self.name, task)
        self._init_context(run_id, task)

        client = self._get_client()
        messages: list[dict] = [{"role": "user", "content": task}]
        max_turns = 10

        for _turn in range(max_turns):
            t0 = time.perf_counter()
            response = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                tools=_TOOL_SCHEMAS,
                messages=messages,
            )
            latency_ms = (time.perf_counter() - t0) * 1000

            # Record LLM call with real token counts from API response
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = calculate_llm_cost(self.model, input_tokens, output_tokens)

            from runcore.core.models import LLMCall
            llm_call = LLMCall(
                model=self.model,
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                cost=cost,
                latency_ms=latency_ms,
            )
            trace = self.collector.get_trace(run_id)
            trace.llm_calls.append(llm_call)

            # Process content blocks
            tool_use_blocks = []
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_use_blocks.append(block)

            # Append assistant message to conversation
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn" or not tool_use_blocks:
                # Agent finished
                break

            # Execute tool calls and feed results back
            tool_results = []
            for tb in tool_use_blocks:
                t_start = time.perf_counter()
                result = _execute_tool(tb.name, tb.input)
                t_latency = (time.perf_counter() - t_start) * 1000
                success = "error" not in str(result).lower()

                self.collector.record_tool_call(
                    run_id, tb.name, tb.input, result, success, t_latency,
                    tokens_used=len(json.dumps(result)) // 4,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": json.dumps(result),
                })

            messages.append({"role": "user", "content": tool_results})

        # Recompute totals
        t = self.collector.get_trace(run_id)
        t.total_cost = sum(c.cost for c in t.llm_calls) + sum(tc.cost for tc in t.tool_calls)
        t.total_tokens = sum(c.total_tokens for c in t.llm_calls) + sum(tc.tokens_used for tc in t.tool_calls)

        self.collector.end_run(run_id, success=True, quality_score=0.90)
        return self.collector.get_trace(run_id)

    def is_available(self) -> bool:
        return bool(self._api_key)
