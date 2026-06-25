"""Base classes for RunCore provider adapters."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    role: str           # "user" | "assistant" | "system" | "tool"
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None  # outgoing tool calls from assistant


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class ProviderResponse:
    content: str | None
    tool_calls: list[dict]         # [{id, name, arguments: dict}]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: float
    model: str
    stop_reason: str               # "end_turn" | "tool_use" | "max_tokens" | "stop"
    raw: Any = None                # original provider response object

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def used_tools(self) -> bool:
        return bool(self.tool_calls)


class BaseProvider(ABC):
    """Abstract base for all RunCore provider adapters."""

    name: str = "unknown"
    model: str = "unknown"

    # Pricing per million tokens (input, output) in USD
    # Override in subclass. Groq/Gemini free tier → $0.
    price_input_per_mtok: float = 0.0
    price_output_per_mtok: float = 0.0

    def __init__(self, model: str | None = None, api_key: str | None = None, **kwargs):
        if model:
            self.model = model
        self._api_key = api_key

    def compute_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * self.price_input_per_mtok
            + output_tokens / 1_000_000 * self.price_output_per_mtok
        )

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        """Send a chat completion request and return a normalized ProviderResponse."""

    def is_available(self) -> bool:
        """Return True if this provider can be used (API key present, etc.)."""
        return True

    @classmethod
    def from_env(cls) -> "BaseProvider":
        """Create instance using environment variable for API key."""
        return cls()
