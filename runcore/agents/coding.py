"""Simulated coding agent."""
from __future__ import annotations

import random

from runcore.agents.base import BaseAgent
from runcore.core.models import AgentTrace
from runcore.tools.registry import ToolRegistry, ToolSchema
from runcore.core.enums import ToolCategory

_registry = ToolRegistry()
for _s in [
    ToolSchema("read_file", "Read file contents", {"type": "object", "properties": {"path": {"type": "string"}}}, ToolCategory.READ, ["path"]),
    ToolSchema("edit_file", "Edit file contents", {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}, ToolCategory.WRITE, ["path", "content"]),
    ToolSchema("run_tests", "Run test suite", {"type": "object", "properties": {"path": {"type": "string"}, "verbose": {"type": "boolean"}}}, ToolCategory.COMPUTE, []),
]:
    _registry.register(_s)

_SYSTEM_PROMPT = (
    "You are an expert software engineer. Your job is to read code, identify bugs, "
    "apply minimal targeted fixes, and verify the fix by running the test suite. "
    "Read the relevant files before editing. Make one focused change at a time. "
    "Always run tests after editing to confirm the fix works."
)

_BUGGY_CODE = """\
def calculate_discount(price: float, pct: float) -> float:
    \"\"\"Return price after discount.\"\"\"
    # BUG: should be (1 - pct/100) not (1 + pct/100)
    return price * (1 + pct / 100)

def apply_tax(price: float, tax_rate: float) -> float:
    return price * (1 + tax_rate)

def final_price(price: float, discount_pct: float, tax_rate: float) -> float:
    discounted = calculate_discount(price, discount_pct)
    return apply_tax(discounted, tax_rate)
"""

_FIXED_CODE = """\
def calculate_discount(price: float, pct: float) -> float:
    \"\"\"Return price after discount.\"\"\"
    return price * (1 - pct / 100)

def apply_tax(price: float, tax_rate: float) -> float:
    return price * (1 + tax_rate)

def final_price(price: float, discount_pct: float, tax_rate: float) -> float:
    discounted = calculate_discount(price, discount_pct)
    return apply_tax(discounted, tax_rate)
"""


class CodingAgent(BaseAgent):
    name = "coding"
    _system_prompt = _SYSTEM_PROMPT

    def __init__(self, optimization=None) -> None:
        super().__init__(optimization=optimization)
        self.tools = _registry.list_all()

    def _compute_quality(self, run_id: str) -> float:
        signals = self._run_signals.get(run_id, {})
        calls = signals.get("tools_called", [])
        total = len(calls)

        # Must have called run_tests with success
        tests_passed = any(n == "run_tests" and ok for n, ok in calls)
        if not tests_passed:
            return 0.40

        # Penalise redundant file reads (loop waste)
        unique = len({n for n, _ in calls})
        dup_penalty = max(0.0, (total - unique) / total) * 0.20

        return max(0.60, round(1.0 - dup_penalty, 3))

    def run(self, task: str) -> AgentTrace:
        run_id = self.collector.start_run(self.name, task)
        self._init_context(run_id, task)

        # Analyse task, plan approach.
        self._simulate_llm_call(run_id,
            "I need to fix a bug in the codebase. Let me start by reading the relevant file "
            "to understand the current implementation before making any changes."
        )

        # Read file
        file_path = "src/pricing.py"
        self._simulate_tool_call(run_id, "read_file", {"path": file_path}, _BUGGY_CODE)

        # Loop waste: 65% chance of reading same file again (agent re-reads to confirm)
        if random.random() < 0.65:
            self._simulate_tool_call(run_id, "read_file", {"path": file_path}, _BUGGY_CODE)

        # Analyse the bug and plan the fix.
        self._simulate_llm_call(run_id,
            "I found the bug. In `calculate_discount`, the formula uses `(1 + pct/100)` "
            "which *adds* the percentage instead of subtracting it. The correct formula is "
            "`(1 - pct/100)`. This is a one-line fix. I'll apply it now."
        )

        # Apply the fix.
        self._simulate_tool_call(run_id, "edit_file", {"path": file_path, "content": _FIXED_CODE},
            {"status": "saved", "lines_changed": 1})

        # Run tests to verify.
        tests_pass = random.random() > 0.1
        self._simulate_tool_call(run_id, "run_tests", {"path": "tests/", "verbose": True},
            {"passed": 12, "failed": 0 if tests_pass else 1, "errors": 0,
             "duration_s": round(random.uniform(0.8, 2.4), 2)},
            success=tests_pass)

        if not tests_pass:
            # Tests failed — re-read, fix again.
            self._simulate_llm_call(run_id,
                "One test is failing. Let me re-read the file to check if I missed something."
            )
            self._simulate_tool_call(run_id, "read_file", {"path": file_path}, _FIXED_CODE)
            self._simulate_tool_call(run_id, "edit_file",
                {"path": file_path, "content": _FIXED_CODE},
                {"status": "saved", "lines_changed": 0})
            self._simulate_tool_call(run_id, "run_tests", {"path": "tests/", "verbose": True},
                {"passed": 12, "failed": 0, "errors": 0, "duration_s": 1.2}, success=True)
            tests_pass = True

        # Summarise the change.
        self._simulate_llm_call(run_id,
            f"Fixed: changed `(1 + pct/100)` to `(1 - pct/100)` in `calculate_discount`. "
            "All 12 tests pass. The bug was a sign error — the discount was being added "
            "instead of subtracted from the price."
        )

        quality = self._compute_quality(run_id)
        self.collector.end_run(run_id, success=tests_pass, quality_score=quality)
        return self.collector.get_trace(run_id)
