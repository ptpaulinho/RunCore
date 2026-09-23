"""Standardised benchmark tasks with ground truth and evaluation criteria."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BenchmarkTask:
    id: str
    name: str
    system_prompt: str
    user_message: str
    tools: list[dict]            # tool definitions
    tool_responses: dict         # tool_name -> response to return
    expected_tools_called: list[str]  # minimum set of tools that must be called
    success_keywords: list[str]  # words that must appear in final answer
    max_turns: int = 6


# ---------------------------------------------------------------------------
# Customer Support agent tasks
# These tasks have deliberate inefficiency patterns:
# - The system prompt causes the LLM to re-look up information it already has
# - Some tools are redundant when used together
# ---------------------------------------------------------------------------

SUPPORT_SYSTEM = """\
You are a meticulous customer support agent. Follow this EXACT procedure for every issue, \
without skipping any step:
1. Look up the customer account with lookup_customer.
2. Look up the order details with lookup_order.
3. Check the order status with check_order_status.
4. MANDATORY re-verification: before issuing ANY resolution you MUST call lookup_customer \
AGAIN with the same email to re-confirm the account is still active. This re-check is required \
by company policy on every ticket — never skip it.
5. Issue the appropriate resolution (e.g. issue_refund).
Always perform the step-4 re-verification of the customer account before taking action."""

SUPPORT_TOOLS = [
    {
        "name": "lookup_customer",
        "description": "Look up customer account by email",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Customer email address"}
            },
            "required": ["email"],
        },
    },
    {
        "name": "lookup_order",
        "description": "Look up order details by order ID",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "check_order_status",
        "description": "Check the current status of an order",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "issue_refund",
        "description": "Issue a refund for an order",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID"},
                "amount": {"type": "number", "description": "Refund amount in USD"},
                "reason": {"type": "string", "description": "Reason for refund"},
            },
            "required": ["order_id", "amount", "reason"],
        },
    },
]

SUPPORT_TOOL_RESPONSES = {
    "lookup_customer": {
        "customer_id": "C-1001",
        "name": "João Silva",
        "email": "joao@example.com",
        "account_status": "active",
        "total_orders": 12,
        "lifetime_value_usd": 1843.27,
        "loyalty_tier": "gold",
        "address": {"street": "Rua das Flores 123", "city": "Lisboa", "postal": "1200-001", "country": "Portugal"},
        "phone": "+351 912 345 678",
        "created_at": "2021-03-14",
        "recent_orders": [
            {"order_id": "ORD-5523", "total": 89.99, "status": "delivered", "date": "2026-06-01"},
            {"order_id": "ORD-5410", "total": 142.50, "status": "delivered", "date": "2026-05-12"},
            {"order_id": "ORD-5288", "total": 67.00, "status": "delivered", "date": "2026-04-03"},
            {"order_id": "ORD-5102", "total": 215.99, "status": "delivered", "date": "2026-02-20"},
            {"order_id": "ORD-4987", "total": 33.49, "status": "refunded", "date": "2026-01-08"},
        ],
        "support_notes": "Customer has contacted support 3 times in the past year regarding shipping delays. All resolved satisfactorily. Prefers email contact. No outstanding complaints.",
    },
    "lookup_order": {
        "order_id": "ORD-5523",
        "customer_id": "C-1001",
        "items": [{"name": "Wireless Headphones", "price": 89.99, "qty": 1}],
        "total": 89.99,
        "created_at": "2026-06-01",
    },
    "check_order_status": {
        "order_id": "ORD-5523",
        "status": "delivered",
        "delivered_at": "2026-06-05",
        "carrier": "DHL",
        "tracking": "1Z999AA10123456784",
    },
    "issue_refund": {
        "refund_id": "REF-887",
        "order_id": "ORD-5523",
        "amount": 89.99,
        "status": "approved",
        "eta": "3-5 business days",
    },
}

SUPPORT_TASKS = [
    BenchmarkTask(
        id="support_refund_1",
        name="Process refund for delivered order",
        system_prompt=SUPPORT_SYSTEM,
        user_message="Hi, I ordered wireless headphones (order ORD-5523) from joao@example.com but they arrived damaged. I'd like a refund.",
        tools=SUPPORT_TOOLS,
        tool_responses=SUPPORT_TOOL_RESPONSES,
        expected_tools_called=["lookup_customer", "lookup_order", "issue_refund"],
        success_keywords=["refund", "approved"],
    ),
    BenchmarkTask(
        id="support_status_1",
        name="Check order status",
        system_prompt=SUPPORT_SYSTEM,
        user_message="Can you check the status of my order ORD-5523? My email is joao@example.com.",
        tools=SUPPORT_TOOLS,
        tool_responses=SUPPORT_TOOL_RESPONSES,
        expected_tools_called=["check_order_status"],
        success_keywords=["delivered", "DHL"],
    ),
    BenchmarkTask(
        id="support_refund_2",
        name="Process refund — item not received",
        system_prompt=SUPPORT_SYSTEM,
        user_message="I never received my order ORD-5523. Email: joao@example.com. Please process a refund.",
        tools=SUPPORT_TOOLS,
        tool_responses=SUPPORT_TOOL_RESPONSES,
        expected_tools_called=["lookup_order", "issue_refund"],
        success_keywords=["refund"],
    ),
]


# ---------------------------------------------------------------------------
# Research agent tasks
# These tasks tend to generate loops — the agent keeps searching for more info
# ---------------------------------------------------------------------------

RESEARCH_SYSTEM = """\
You are a research assistant. When asked about a topic:
1. Search for general information
2. Search for recent news
3. Search for expert opinions
4. If you find conflicting information, search again to clarify
5. Summarize your findings

Be thorough — search multiple times if needed."""

RESEARCH_TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for information",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": "Fetch the content of a URL",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "summarize_text",
        "description": "Summarize a long piece of text",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to summarize"},
                "max_words": {"type": "integer", "description": "Max words in summary", "default": 100},
            },
            "required": ["text"],
        },
    },
]

RESEARCH_TOOL_RESPONSES = {
    "web_search": {
        "results": [
            {"title": "LLM Cost Optimization — Best Practices 2026", "url": "https://example.com/llm-cost", "snippet": "Key strategies include caching, batching, and reducing duplicate API calls..."},
            {"title": "Reducing AI Agent Costs in Production", "url": "https://example.com/agent-cost", "snippet": "Studies show 30-60% of LLM spend is wasted on redundant operations..."},
            {"title": "RunCore: Runtime Optimization for LLM Agents", "url": "https://pypi.org/project/runcore", "snippet": "92% CpST reduction demonstrated on production workloads..."},
        ],
    },
    "fetch_url": {
        "content": "This article discusses how AI agents waste significant compute by making duplicate API calls. The main causes are: 1) No deduplication of tool calls, 2) Context growing unbounded, 3) Lack of loop detection. Solutions include runtime guards, context compression, and loop breakers.",
        "url": "https://example.com/llm-cost",
        "word_count": 312,
    },
    "summarize_text": {
        "summary": "AI agents waste 30-60% of LLM spend on redundant operations. Key solutions: dedup guards, context compression, loop detection. RunCore provides all three with 92% CpST reduction.",
        "original_words": 312,
        "summary_words": 42,
    },
}

RESEARCH_TASKS = [
    BenchmarkTask(
        id="research_llm_cost_1",
        name="Research LLM cost optimization",
        system_prompt=RESEARCH_SYSTEM,
        user_message="Research the best practices for reducing LLM API costs for AI agents in production.",
        tools=RESEARCH_TOOLS,
        tool_responses=RESEARCH_TOOL_RESPONSES,
        expected_tools_called=["web_search"],
        success_keywords=["cost", "optimization"],
        max_turns=8,
    ),
    BenchmarkTask(
        id="research_llm_cost_2",
        name="Research duplicate call waste in agents",
        system_prompt=RESEARCH_SYSTEM,
        user_message="Find information about how much money companies waste on duplicate LLM API calls.",
        tools=RESEARCH_TOOLS,
        tool_responses=RESEARCH_TOOL_RESPONSES,
        expected_tools_called=["web_search"],
        success_keywords=["waste", "duplicate"],
        max_turns=8,
    ),
]


# ---------------------------------------------------------------------------
# Coding agent tasks
# These tasks expose loop-prone behaviour (agent re-reads file multiple times)
# ---------------------------------------------------------------------------

CODING_SYSTEM = """\
You are a senior software engineer debugging production issues.
When given a bug report:
1. Read the relevant file
2. Search for the error pattern in the codebase
3. Read the file again to understand context
4. Identify the root cause
5. Write the fix
6. Run tests to verify

Be thorough — read files multiple times if needed to understand the full context."""

CODING_TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": "Search for a pattern in the codebase",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "file_glob": {"type": "string", "default": "**/*.py"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run the test suite",
        "parameters": {
            "type": "object",
            "properties": {"test_path": {"type": "string", "default": "tests/"}},
        },
    },
]

CODING_TOOL_RESPONSES = {
    "read_file": {
        "path": "src/billing.py",
        "content": "def calculate_total(items):\n    total = 0\n    for item in items:\n        total += item['price'] * item['qty']\n    return total  # BUG: missing tax calculation",
        "lines": 6,
    },
    "search_code": {
        "matches": [
            {"file": "src/billing.py", "line": 4, "content": "    total += item['price'] * item['qty']"},
            {"file": "tests/test_billing.py", "line": 12, "content": "assert calculate_total(items) == 107.98  # includes 10% tax"},
        ],
    },
    "write_file": {
        "path": "src/billing.py",
        "bytes_written": 201,
        "status": "ok",
    },
    "run_tests": {
        "passed": 14,
        "failed": 0,
        "duration_ms": 340,
        "status": "all passed",
    },
}

CODING_TASKS = [
    BenchmarkTask(
        id="coding_bug_fix_1",
        name="Fix missing tax calculation bug",
        system_prompt=CODING_SYSTEM,
        user_message="Bug report: calculate_total() in src/billing.py returns wrong amount — tests expect 10% tax but it's not applied. Fix it.",
        tools=CODING_TOOLS,
        tool_responses=CODING_TOOL_RESPONSES,
        expected_tools_called=["read_file", "write_file"],
        success_keywords=["tax", "fix"],
        max_turns=8,
    ),
    BenchmarkTask(
        id="coding_test_failure_1",
        name="Diagnose failing test",
        system_prompt=CODING_SYSTEM,
        user_message="CI is failing on test_billing.py line 12. The test expects 10% tax but calculate_total() doesn't include it. Find and fix the issue.",
        tools=CODING_TOOLS,
        tool_responses=CODING_TOOL_RESPONSES,
        expected_tools_called=["read_file", "run_tests"],
        success_keywords=["fixed", "pass"],
        max_turns=8,
    ),
]


# ---------------------------------------------------------------------------
# Data analysis agent tasks
# These test context window management — large data payloads
# ---------------------------------------------------------------------------

ANALYTICS_SYSTEM = """\
You are a data analyst. When asked to analyse data:
1. Fetch the dataset
2. Compute summary statistics
3. Identify outliers
4. Fetch the dataset again if needed for verification
5. Generate insights and recommendations

Always double-check your numbers."""

ANALYTICS_TOOLS = [
    {
        "name": "fetch_dataset",
        "description": "Fetch a dataset by name",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "limit": {"type": "integer", "default": 1000}},
            "required": ["name"],
        },
    },
    {
        "name": "compute_stats",
        "description": "Compute statistics for a numeric column",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "column": {"type": "string"},
            },
            "required": ["dataset", "column"],
        },
    },
    {
        "name": "filter_rows",
        "description": "Filter dataset rows by condition",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "condition": {"type": "string"},
            },
            "required": ["dataset", "condition"],
        },
    },
]

ANALYTICS_TOOL_RESPONSES = {
    "fetch_dataset": {
        "name": "agent_runs",
        "rows": 1247,
        "columns": ["run_id", "agent", "cost_usd", "tokens", "duration_ms", "success"],
        "sample": [
            {"run_id": "r001", "agent": "support", "cost_usd": 0.0082, "tokens": 1840, "duration_ms": 2100, "success": True},
            {"run_id": "r002", "agent": "support", "cost_usd": 0.0059, "tokens": 1320, "duration_ms": 1800, "success": True},
        ],
    },
    "compute_stats": {
        "column": "cost_usd",
        "mean": 0.0071,
        "median": 0.0068,
        "std": 0.0018,
        "min": 0.0031,
        "max": 0.0142,
        "p95": 0.0112,
    },
    "filter_rows": {
        "filtered_rows": 187,
        "condition": "cost_usd > 0.01",
        "pct_of_total": 15.0,
    },
}

ANALYTICS_TASKS = [
    BenchmarkTask(
        id="analytics_cost_1",
        name="Analyse agent cost distribution",
        system_prompt=ANALYTICS_SYSTEM,
        user_message="Analyse the cost distribution of agent runs in the agent_runs dataset. Identify outliers and recommend optimizations.",
        tools=ANALYTICS_TOOLS,
        tool_responses=ANALYTICS_TOOL_RESPONSES,
        expected_tools_called=["fetch_dataset", "compute_stats"],
        success_keywords=["cost", "outlier"],
        max_turns=8,
    ),
]


# ---------------------------------------------------------------------------
# All tasks
# ---------------------------------------------------------------------------

ALL_TASKS: dict[str, list[BenchmarkTask]] = {
    "support": SUPPORT_TASKS,
    "research": RESEARCH_TASKS,
    "coding": CODING_TASKS,
    "analytics": ANALYTICS_TASKS,
}


def get_task(task_id: str) -> BenchmarkTask | None:
    for tasks in ALL_TASKS.values():
        for t in tasks:
            if t.id == task_id:
                return t
    return None


# ---------------------------------------------------------------------------
# Hidden, rotating suites — the public tasks above are only the *shape*.
#
# A certificate is only worth something if the vendor cannot train on the exam.
# Two layers:
#   1. rotation: every identifier (ORD-5523, joao@example.com, ...) is replaced,
#      consistently across prompt, tool responses and grading, from an HMAC of a
#      server-side secret and the month; tool order is shuffled. Memorising this
#      file does not help.
#   2. private tasks: RUNCORE_PRIVATE_SUITE points to a JSON list of tasks that
#      never enter the repository.
# The certificate records suite_version (an HMAC, not the secret), so a result is
# tied to the exact exam it passed.
# ---------------------------------------------------------------------------

import hashlib as _hashlib
import hmac as _hmac
import json as _json
import os as _os
import random as _random
import re as _re
from dataclasses import asdict as _asdict

_ID_RE = _re.compile(r"\b([A-Z]{1,5})-(\d{3,6})\b")
_EMAIL_RE = _re.compile(r"\b([a-z0-9._]+)@(example\.com)\b")
_FIRST = ["ana", "rui", "marta", "tiago", "ines", "pedro", "sofia", "miguel", "carla", "nuno"]


def current_period(now=None) -> str:
    from datetime import datetime, timezone
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def suite_version(secret: str, period: str) -> str:
    return _hmac.new(secret.encode(), f"suite:{period}".encode(), _hashlib.sha256).hexdigest()[:12]


def rotate_tasks(tasks: list[BenchmarkTask], secret: str, period: str) -> list[BenchmarkTask]:
    """Same tasks, different identifiers and tool order — deterministic per (secret, period)."""
    def h(token: str) -> int:
        return int(_hmac.new(secret.encode(), f"{period}:{token}".encode(), _hashlib.sha256).hexdigest(), 16)

    def sub_id(m):
        digits = m.group(2)
        lo = 10 ** (len(digits) - 1)
        return f"{m.group(1)}-{lo + h(m.group(0)) % (9 * lo)}"

    def sub_email(m):
        return f"{_FIRST[h(m.group(0)) % len(_FIRST)]}{h(m.group(0)) % 900 + 100}@{m.group(2)}"

    out = []
    for t in tasks:
        raw = _EMAIL_RE.sub(sub_email, _ID_RE.sub(sub_id, _json.dumps(_asdict(t), ensure_ascii=False)))
        new = BenchmarkTask(**_json.loads(raw))
        new.tools = list(new.tools)
        _random.Random(h(t.id)).shuffle(new.tools)
        out.append(new)
    return out


def load_private_tasks(path: str | None = None) -> list[BenchmarkTask]:
    path = path or _os.environ.get("RUNCORE_PRIVATE_SUITE", "")
    if not path or not _os.path.isfile(path):
        return []
    with open(path) as f:
        return [BenchmarkTask(**t) for t in _json.load(f)]


def certification_tasks(suite: str, secret: str | None = None, period: str | None = None
                        ) -> tuple[list[BenchmarkTask], str]:
    """Tasks for a certification run and their suite_version ('public' when no secret is set)."""
    if suite == "all":
        tasks = [t for ts_list in ALL_TASKS.values() for t in ts_list]
    else:
        tasks = list(ALL_TASKS.get(suite, []))
        if not tasks:
            raise ValueError(f"Unknown suite: {suite}. Available: {list(ALL_TASKS.keys()) + ['all']}")
    secret = secret if secret is not None else _os.environ.get("RUNCORE_SUITE_SECRET", "")
    if not secret:
        return tasks, "public"
    period = period or current_period()
    private = [t for t in load_private_tasks() if suite == "all" or t.id.startswith(suite)]
    return rotate_tasks(tasks, secret, period) + private, suite_version(secret, period)
