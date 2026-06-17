"""Simulated customer support agent."""
from __future__ import annotations

import random

from runcore.agents.base import BaseAgent
from runcore.core.models import AgentTrace
from runcore.tools.registry import ToolRegistry, ToolSchema
from runcore.core.enums import ToolCategory

_registry = ToolRegistry()
for _s in [
    ToolSchema("get_invoice", "Retrieve invoice by ID", {"type": "object", "properties": {"invoice_id": {"type": "string"}}, "required": ["invoice_id"]}, ToolCategory.READ, ["invoice_id"]),
    ToolSchema("get_customer", "Get customer record by email", {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}, ToolCategory.READ, ["email"]),
    ToolSchema("search_docs", "Search support documentation", {"type": "object", "properties": {"query": {"type": "string"}}}, ToolCategory.SEARCH, ["query"]),
    ToolSchema("refund_order", "Process refund for an order", {"type": "object", "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"}}}, ToolCategory.WRITE, ["order_id"]),
]:
    _registry.register(_s)

_SYSTEM_PROMPT = (
    "You are a customer support agent for an e-commerce platform. "
    "Your job is to resolve customer issues efficiently and accurately. "
    "You have access to tools to look up invoices, customer accounts, "
    "search documentation, and process refunds. "
    "Always verify customer identity before processing any financial action. "
    "Be concise, professional, and resolve issues in as few steps as possible."
)


class SupportAgent(BaseAgent):
    name = "support"
    _system_prompt = _SYSTEM_PROMPT

    def __init__(self, optimization=None) -> None:
        super().__init__(optimization=optimization)
        self.tools = _registry.list_all()

    def _compute_quality(self, run_id: str) -> float:
        signals = self._run_signals.get(run_id, {})
        calls = signals.get("tools_called", [])
        names_ok = {n for n, ok in calls if ok}
        total = len(calls)

        # Task only completes if refund_order was successfully called
        if "refund_order" not in names_ok:
            return 0.30

        # Penalise duplicates (real duplicate waste)
        unique = len({n for n, _ in calls})
        dup_penalty = max(0.0, (total - unique) / total) * 0.25

        return max(0.50, round(1.0 - dup_penalty, 3))

    # Realistic verbose tool results that make context grow — typical in production
    _CUSTOMER_RESULT = {
        "id": "cust_42", "name": "John Doe", "email": "customer@example.com",
        "tier": "standard", "since": "2022-01-15", "orders": 14, "lifetime_value": 1240.50,
        "address": "123 Main St, Springfield, IL 62701", "phone": "+1-555-0100",
        "preferences": {"newsletter": True, "sms": False, "refund_method": "original_payment"},
        "notes": "Long-standing customer, no previous disputes. Auto-approve refunds under $200.",
        "last_login": "2024-05-09T14:32:11Z",
    }
    _INVOICE_RESULT = {
        "id": "INV-1001", "amount": 99.99, "status": "paid", "date": "2024-05-10",
        "items": [{"sku": "PROD-7", "name": "Premium Widget", "qty": 1, "unit_price": 99.99, "tax": 0.0}],
        "payment_method": "credit_card", "card_last4": "4242", "currency": "USD",
        "billing_address": "123 Main St, Springfield, IL 62701",
        "refund_eligible": True, "refund_window_days": 30, "days_since_purchase": 6,
        "transaction_id": "txn_3NxOW2LkdIwHu7ix0Y0VKQGE",
    }

    def run(self, task: str) -> AgentTrace:
        run_id = self.collector.start_run(self.name, task)
        self._init_context(run_id, task)

        # Initial reasoning — analyse the task and decide which tools to call.
        self._simulate_llm_call(run_id,
            "I need to resolve this customer support request. Let me look up the customer "
            "account first, then retrieve the relevant invoice, and finally process the refund "
            "if the request is valid. I'll start by getting the customer record to verify identity "
            "and confirm account standing before accessing financial records."
        )

        # Realistic waste: 65% chance of unnecessary search_docs before acting
        if random.random() < 0.65:
            self._simulate_tool_call(run_id, "search_docs",
                {"query": "refund policy eligibility requirements"},
                {"results": [
                    "Refund Policy: Items purchased within 30 days are eligible for a full refund.",
                    "Processing time: 3-5 business days after approval.",
                    "Exceptions: Digital goods and gift cards are non-refundable.",
                    "Required: Customer must provide order ID and reason for return.",
                ]})

        # Get customer
        email = "customer@example.com"
        self._simulate_tool_call(run_id, "get_customer", {"email": email}, self._CUSTOMER_RESULT)

        # Get invoice
        invoice_id = "INV-1001"
        self._simulate_tool_call(run_id, "get_invoice", {"invoice_id": invoice_id}, self._INVOICE_RESULT)

        # Second LLM reasoning
        self._simulate_llm_call(run_id,
            "I have the customer account (cust_42, John Doe) and the invoice INV-1001 for $99.99. "
            "The invoice is in 'paid' status and was purchased within the refund window (6 days ago, "
            "limit is 30 days). The customer is a long-standing account with no disputes. "
            "Auto-approve threshold is $200 and this is $99.99, so I can proceed. "
            "The request is valid. I will now process the refund to the original payment method."
        )

        # Loop waste: 60% chance of re-fetching the invoice (agent lost track of context)
        if random.random() < 0.60:
            self._simulate_tool_call(run_id, "get_invoice", {"invoice_id": invoice_id},
                self._INVOICE_RESULT)

        # Additional loop waste: 45% chance of re-running the same search (agent forgot results)
        if random.random() < 0.45:
            self._simulate_tool_call(run_id, "search_docs",
                {"query": "refund policy eligibility requirements"},
                {"results": [
                    "Refund Policy: Items purchased within 30 days are eligible for a full refund.",
                    "Processing time: 3-5 business days after approval.",
                ]})

        # Process refund
        self._simulate_tool_call(run_id, "refund_order", {"order_id": invoice_id, "amount": 99.99},
            {"status": "refunded", "ref": "REF-9901", "eta_days": 3})

        # Final LLM response — compose the reply to the customer.
        self._simulate_llm_call(run_id,
            "Your refund of $99.99 for invoice INV-1001 has been processed successfully. "
            "Reference number: REF-9901. Please allow 3 business days for the amount to appear "
            "on your statement. Is there anything else I can help you with?"
        )

        quality = self._compute_quality(run_id)
        self.collector.end_run(run_id, success=True, quality_score=quality)
        return self.collector.get_trace(run_id)
