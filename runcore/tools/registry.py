"""Tool registry for RunCore - manages tool schemas and token estimation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from runcore.core import ToolCategory


@dataclass
class ToolSchema:
    """Schema definition for a single tool."""

    name: str
    description: str
    parameters: Dict  # JSON schema dict
    category: ToolCategory
    required: List[str] = field(default_factory=list)
    token_count: int = 0

    def __post_init__(self) -> None:
        if self.token_count == 0:
            self.token_count = _estimate_tokens_for_schema(self)


def _estimate_tokens_for_schema(schema: ToolSchema) -> int:
    """Estimate tokens for a single schema using a simple heuristic."""
    # Serialize to JSON and count characters / 4 as rough token estimate
    data = {
        "name": schema.name,
        "description": schema.description,
        "parameters": schema.parameters,
        "required": schema.required,
    }
    text = json.dumps(data)
    return max(1, len(text) // 4)


class ToolRegistry:
    """Registry for managing tool schemas."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSchema] = {}

    def register(self, schema: ToolSchema) -> None:
        """Register a tool schema."""
        self._tools[schema.name] = schema

    def get(self, name: str) -> ToolSchema:
        """Retrieve a tool schema by name. Raises KeyError if not found."""
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found in registry.")
        return self._tools[name]

    def list_all(self) -> List[ToolSchema]:
        """Return all registered tool schemas."""
        return list(self._tools.values())

    def estimate_schema_tokens(self, names: List[str]) -> int:
        """Estimate token count for the given tool names (sum of individual counts)."""
        total = 0
        for name in names:
            schema = self.get(name)
            total += schema.token_count
        return total

    def total_token_cost(self, names: List[str]) -> int:
        """Alias for estimate_schema_tokens - total token cost for a list of tools."""
        return self.estimate_schema_tokens(names)
