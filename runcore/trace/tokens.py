"""Token counting utilities for RunCore trace module."""
from __future__ import annotations

from typing import Any

MODEL_COSTS: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4":                     {"input": 0.03 / 1000,    "output": 0.06 / 1000},
    "gpt-4-turbo":               {"input": 0.01 / 1000,    "output": 0.03 / 1000},
    "gpt-4o":                    {"input": 0.0025 / 1000,  "output": 0.01 / 1000},
    "gpt-4o-mini":               {"input": 0.00015 / 1000, "output": 0.0006 / 1000},
    "gpt-3.5-turbo":             {"input": 0.001 / 1000,   "output": 0.002 / 1000},
    # Anthropic — Claude 3.x
    "claude-3-5-sonnet-20241022":{"input": 0.003 / 1000,   "output": 0.015 / 1000},
    "claude-3-5-haiku-20241022": {"input": 0.0008 / 1000,  "output": 0.004 / 1000},
    "claude-3-opus-20240229":    {"input": 0.015 / 1000,   "output": 0.075 / 1000},
    "claude-3-haiku-20240307":   {"input": 0.00025 / 1000, "output": 0.00125 / 1000},
    # Anthropic — Claude 4.x (2025)
    "claude-sonnet-4-6":         {"input": 0.003 / 1000,   "output": 0.015 / 1000},
    "claude-haiku-4-5-20251001": {"input": 0.0008 / 1000,  "output": 0.004 / 1000},
    "claude-opus-4-8":           {"input": 0.015 / 1000,   "output": 0.075 / 1000},
    # Groq (free tier — $0 cost, non-zero for paid)
    "llama3-8b-8192":            {"input": 0.0,             "output": 0.0},
    "llama3-70b-8192":           {"input": 0.0,             "output": 0.0},
    "llama-3.1-8b-instant":      {"input": 0.0,             "output": 0.0},
    "llama-3.3-70b-versatile":   {"input": 0.0,             "output": 0.0},
    "mixtral-8x7b-32768":        {"input": 0.0,             "output": 0.0},
    "gemma2-9b-it":              {"input": 0.0,             "output": 0.0},
    # Gemini (free tier — $0 cost)
    "gemini-1.5-flash":          {"input": 0.0,             "output": 0.0},
    "gemini-1.5-pro":            {"input": 0.0,             "output": 0.0},
    "gemini-2.0-flash":          {"input": 0.0,             "output": 0.0},
    "gemini-2.5-flash":          {"input": 0.0,             "output": 0.0},
    # Ollama (fully local — $0)
    "llama3":                    {"input": 0.0,             "output": 0.0},
    "llama3.1":                  {"input": 0.0,             "output": 0.0},
    "mistral":                   {"input": 0.0,             "output": 0.0},
    "qwen2.5":                   {"input": 0.0,             "output": 0.0},
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
