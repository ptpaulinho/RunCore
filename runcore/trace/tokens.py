"""Token counting utilities for RunCore trace module."""
from __future__ import annotations

from typing import Any

MODEL_COSTS: dict[str, dict[str, float]] = {
    "gpt-4": {"input": 0.03 / 1000, "output": 0.06 / 1000},
    "gpt-3.5-turbo": {"input": 0.001 / 1000, "output": 0.002 / 1000},
    "claude-3-5-sonnet-20241022": {"input": 0.003 / 1000, "output": 0.015 / 1000},
    "claude-3-haiku-20240307": {"input": 0.00025 / 1000, "output": 0.00125 / 1000},
}


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """Count tokens in text for a given model using tiktoken, falling back to len(text)//4."""
    try:
        import tiktoken

        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")

        return len(enc.encode(text))
    except ImportError:
        return len(text) // 4


def estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate the total token count for a list of chat messages.

    Each message is expected to have at least a 'content' key (str).
    Role and name fields add a small overhead per OpenAI/Anthropic conventions.
    """
    total = 0
    for message in messages:
        # Per-message overhead (role token + delimiters)
        total += 4

        content = message.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            # Handle multi-part content blocks (e.g. Anthropic vision messages)
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        total += count_tokens(str(text))
                else:
                    total += count_tokens(str(block))

        # Optional name field adds one extra token
        if message.get("name"):
            total += 1

    # Reply priming overhead
    total += 2
    return total
