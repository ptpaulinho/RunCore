"""Simulated research agent."""
from __future__ import annotations

import random

from runcore.agents.base import BaseAgent
from runcore.core.models import AgentTrace
from runcore.tools.registry import ToolRegistry, ToolSchema
from runcore.core.enums import ToolCategory

_registry = ToolRegistry()
for _s in [
    ToolSchema("web_search", "Search the web for information", {"type": "object", "properties": {"query": {"type": "string"}, "num_results": {"type": "integer"}}}, ToolCategory.SEARCH, ["query"]),
    ToolSchema("open_page", "Open and read a web page", {"type": "object", "properties": {"url": {"type": "string"}}}, ToolCategory.READ, ["url"]),
    ToolSchema("summarize", "Summarize a document or text", {"type": "object", "properties": {"text": {"type": "string"}, "max_words": {"type": "integer"}}}, ToolCategory.COMPUTE, ["text"]),
    ToolSchema("extract_facts", "Extract key facts from text", {"type": "object", "properties": {"text": {"type": "string"}, "topic": {"type": "string"}}}, ToolCategory.COMPUTE, ["text"]),
]:
    _registry.register(_s)

_SYSTEM_PROMPT = (
    "You are a research assistant. Your job is to find accurate, up-to-date information "
    "on any topic by searching the web, reading relevant pages, and synthesising the findings "
    "into a clear, factual summary. Always verify information from multiple sources. "
    "Extract specific facts and cite your sources."
)

_PAGE_CONTENT = (
    "This comprehensive article discusses the topic in depth. Key findings include: the phenomenon was "
    "first observed in 2018, affects approximately 23% of cases across all demographics, and has been "
    "replicated across 14 independent studies conducted in 8 different countries. The primary mechanism "
    "involves three distinct pathways: (1) direct pathway via receptor activation, (2) indirect pathway "
    "through metabolic changes, and (3) feedback inhibition loop that self-regulates response intensity. "
    "Researchers at MIT and Stanford have contributed the most significant work, with 47 peer-reviewed "
    "publications between them. The MIT team focused on pathway 1, while Stanford investigated pathway 2. "
    "Current scientific consensus strongly supports the hypothesis with high statistical confidence "
    "(p < 0.001, effect size d=0.82). Practical applications are still being developed, with three "
    "clinical trials currently underway. Funding has come primarily from NIH ($12M over 5 years) "
    "and private foundations. The research community expects significant advances in the next 3-5 years."
)


class ResearchAgent(BaseAgent):
    name = "research"
    _system_prompt = _SYSTEM_PROMPT

    def __init__(self, optimization=None) -> None:
        super().__init__(optimization=optimization)
        self.tools = _registry.list_all()

    def _compute_quality(self, run_id: str) -> float:
        signals = self._run_signals.get(run_id, {})
        calls = signals.get("tools_called", [])
        names_ok = {n for n, ok in calls if ok}
        total = len(calls)

        # Research task requires both extraction and synthesis
        has_extraction = "extract_facts" in names_ok
        has_synthesis = "summarize" in names_ok
        base = 0.40 + (0.30 if has_extraction else 0) + (0.30 if has_synthesis else 0)

        unique = len({n for n, _ in calls})
        dup_penalty = max(0.0, (total - unique) / total) * 0.20

        return max(0.30, round(base - dup_penalty, 3))

    def run(self, task: str) -> AgentTrace:
        run_id = self.collector.start_run(self.name, task)
        self._init_context(run_id, task)

        # Plan the research approach.
        self._simulate_llm_call(run_id,
            f"I'll research '{task}' by first searching for recent information, "
            "then reading the most relevant sources, extracting key facts, and summarising. "
            "Starting with a web search."
        )

        # First search
        query = task
        results = ["https://example.com/article-1", "https://example.com/article-2", "https://example.com/paper-3"]
        self._simulate_tool_call(run_id, "web_search", {"query": query, "num_results": 5}, results)

        # Loop waste: 65% chance of duplicate search (agent rephrased same query)
        if random.random() < 0.65:
            self._simulate_tool_call(run_id, "web_search", {"query": query, "num_results": 5}, results)

        # Open and read pages
        for url in results[:2]:
            self._simulate_tool_call(run_id, "open_page", {"url": url}, _PAGE_CONTENT)

        # Loop waste: 50% chance of re-reading the first page (agent forgot it read it)
        if random.random() < 0.50:
            self._simulate_tool_call(run_id, "open_page", {"url": results[0]}, _PAGE_CONTENT)

        # Reason over collected content.
        self._simulate_llm_call(run_id,
            "I've read two sources with consistent findings. The data shows clear evidence "
            "supporting the main hypothesis. Let me extract the specific facts and numbers "
            "before synthesising the final answer."
        )

        # Extract facts
        self._simulate_tool_call(run_id, "extract_facts", {"text": _PAGE_CONTENT, "topic": task},
            {"facts": [
                "First observed in 2018",
                "Affects approximately 23% of cases",
                "Replicated across 14 independent studies",
                "MIT and Stanford are leading contributors",
            ]})

        # Summarize
        self._simulate_tool_call(run_id, "summarize", {"text": _PAGE_CONTENT, "max_words": 200},
            {"summary": f"Research on '{task}' shows strong evidence from 14 studies. "
                        "The phenomenon affects 23% of cases and involves three distinct mechanisms. "
                        "Confidence is high (p < 0.001)."})

        # Final synthesis.
        self._simulate_llm_call(run_id,
            f"Based on my research, here is a comprehensive summary of '{task}': "
            "The evidence from multiple peer-reviewed sources confirms the main findings. "
            "Key facts: first observed 2018, 23% prevalence, 14 replication studies. "
            "The mechanism involves three pathways, well-documented by MIT and Stanford. "
            "Confidence in these findings is very high."
        )

        quality = self._compute_quality(run_id)
        self.collector.end_run(run_id, success=True, quality_score=quality)
        return self.collector.get_trace(run_id)
