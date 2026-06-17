"""Tool ranking utilities for RunCore - scores and ranks tools by task relevance."""

from __future__ import annotations

import re
from typing import List, Tuple

from runcore.core import ToolCategory
from runcore.tools.registry import ToolSchema

# Category keyword mappings for scoring
_CATEGORY_KEYWORDS: dict[ToolCategory, list[str]] = {
    ToolCategory.SEARCH: [
        "search", "find", "query", "lookup", "web", "internet", "fetch", "browse",
        "retrieve", "get", "url", "http", "api",
    ],
    ToolCategory.READ: [
        "read", "file", "load", "open", "get", "directory", "path", "folder", "disk",
        "contents", "data", "text", "json", "csv", "parse",
    ],
    ToolCategory.WRITE: [
        "write", "save", "create", "delete", "move", "copy", "rename", "update",
        "store", "output", "file", "disk", "send", "post", "upload",
    ],
    ToolCategory.COMPUTE: [
        "compute", "run", "execute", "code", "script", "bash", "shell", "program",
        "compile", "calculate", "process", "transform", "analyze", "test", "debug",
    ],
    ToolCategory.EXTERNAL: [
        "external", "service", "api", "webhook", "email", "message", "notify",
        "slack", "sms", "notification", "integrate", "third-party", "cloud",
    ],
}


def _tokenize(text: str) -> list[str]:
    """Lowercase-tokenize a string into words."""
    return re.findall(r"[a-z0-9]+", text.lower())


def score_tool_relevance(tool: ToolSchema, task: str) -> float:
    """Score a tool's relevance to a task string.

    Combines:
    - Keyword overlap between tool name/description and task
    - Category bonus based on task keywords matching known category keywords
    """
    task_tokens = set(_tokenize(task))
    if not task_tokens:
        return 0.0

    # Keyword overlap score
    tool_text = f"{tool.name} {tool.description}"
    tool_tokens = set(_tokenize(tool_text))
    overlap = task_tokens & tool_tokens
    keyword_score = len(overlap) / len(task_tokens)

    # Category score: check how many category keywords appear in task
    category_keywords = _CATEGORY_KEYWORDS.get(tool.category, [])
    if category_keywords:
        cat_hits = sum(1 for kw in category_keywords if kw in task_tokens)
        category_score = min(1.0, cat_hits / max(1, len(category_keywords) * 0.2))
    else:
        category_score = 0.0

    # Parameter name overlap bonus
    param_tokens: set[str] = set()
    if isinstance(tool.parameters, dict):
        props = tool.parameters.get("properties", {})
        for pname in props:
            param_tokens.update(_tokenize(pname))
    param_overlap = task_tokens & param_tokens
    param_score = len(param_overlap) / len(task_tokens) * 0.5

    combined = keyword_score * 0.5 + category_score * 0.3 + param_score * 0.2
    return round(min(1.0, combined), 4)


def rank_tools_for_task(
    tools: List[ToolSchema], task: str
) -> List[Tuple[ToolSchema, float]]:
    """Return tools sorted by relevance score (descending)."""
    scored = [(tool, score_tool_relevance(tool, task)) for tool in tools]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def select_top_k_tools(
    tools: List[ToolSchema], task: str, k: int
) -> List[ToolSchema]:
    """Select the top-k most relevant tools for a task."""
    ranked = rank_tools_for_task(tools, task)
    return [tool for tool, _ in ranked[:k]]
