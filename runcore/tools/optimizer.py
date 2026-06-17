"""Tool optimizer for RunCore - selects and compresses tools for efficient LLM usage."""

from __future__ import annotations

from typing import Any, Dict, List

from runcore.core import ToolCategory
from runcore.tools.compression import compress_schemas, measure_compression_ratio
from runcore.tools.ranking import rank_tools_for_task
from runcore.tools.registry import ToolSchema


class ToolOptimizer:
    """Optimizes a set of tool schemas for a given task to minimize token usage."""

    def optimize(
        self,
        tools: List[ToolSchema],
        task: str,
        max_tools: int = 5,
        required_tools: List[str] | None = None,
    ) -> List[ToolSchema]:
        """Select and compress the most relevant tools for a task."""
        if not tools:
            return []

        required_names = set(required_tools or [])
        required = [t for t in tools if t.name in required_names]
        candidates = [t for t in tools if t.name not in required_names]

        ranked = rank_tools_for_task(candidates, task)
        slots = max(0, max_tools - len(required))
        top_tools = required + [t for t, _ in ranked[:slots]]

        compressed = compress_schemas(top_tools)
        return compressed

    def generate_tool_manifest(self, tools: List[ToolSchema]) -> Dict[str, Any]:
        """Generate a JSON-serializable manifest dict from a list of schemas."""
        manifest: Dict[str, Any] = {
            "tools": [],
            "total_token_estimate": sum(t.token_count for t in tools),
            "count": len(tools),
        }
        for tool in tools:
            entry: Dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "category": tool.category.value if hasattr(tool.category, "value") else str(tool.category),
                "required": tool.required,
                "token_count": tool.token_count,
            }
            manifest["tools"].append(entry)
        return manifest

    def estimate_savings(
        self,
        original: List[ToolSchema],
        optimized: List[ToolSchema],
    ) -> Dict[str, Any]:
        """Estimate token savings between original and optimized tool lists.

        Returns a dict with:
        - original_tokens: total tokens in original list
        - optimized_tokens: total tokens in optimized list
        - token_reduction: absolute token reduction
        - pct_reduction: percentage reduction (0-100)
        - tools_removed: number of tools dropped
        """
        original_tokens = sum(t.token_count for t in original)
        optimized_tokens = sum(t.token_count for t in optimized)
        token_reduction = original_tokens - optimized_tokens
        pct_reduction = (
            round(token_reduction / original_tokens * 100, 2)
            if original_tokens > 0
            else 0.0
        )
        return {
            "original_tokens": original_tokens,
            "optimized_tokens": optimized_tokens,
            "token_reduction": token_reduction,
            "pct_reduction": pct_reduction,
            "tools_removed": len(original) - len(optimized),
        }
