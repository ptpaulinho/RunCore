"""Groq provider adapter — free tier, very fast inference.

Free models (as of 2026):
  llama-3.1-8b-instant     — fast, great for tool use
  llama-3.3-70b-versatile  — larger, better reasoning
  gemma2-9b-it             — Google Gemma via Groq
  mixtral-8x7b-32768       — Mixtral MoE

Get a free API key: https://console.groq.com
Set env var: GROQ_API_KEY=gsk_...
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from runcore.providers.base import BaseProvider, Message, ProviderResponse, ToolDefinition

_DEFAULT_MODEL = "llama-3.1-8b-instant"


class GroqProvider(BaseProvider):
    name = "groq"
    price_input_per_mtok = 0.0    # free tier
    price_output_per_mtok = 0.0

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
    ):
        super().__init__(model=model, api_key=api_key)
        self.model = model
        self._api_key = api_key or os.environ.get("GROQ_API_KEY", "")

    def is_available(self) -> bool:
        if not self._api_key:
            return False
        try:
            import groq  # noqa: F401
        except ImportError:
            return False
        return True

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        try:
            from groq import Groq
        except ImportError as exc:
            raise ImportError("Install groq: pip install groq>=0.9.0") from exc

        client = Groq(api_key=self._api_key)

        # Convert messages
        groq_messages = []
        for m in messages:
            if m.role == "tool":
                groq_messages.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                })
            elif m.tool_calls:
                groq_messages.append({
                    "role": "assistant",
                    "content": m.content or "",
                    "tool_calls": m.tool_calls,
                })
            else:
                groq_messages.append({"role": m.role, "content": m.content})

        # Convert tools
        groq_tools = None
        if tools:
            groq_tools = [
                {"type": "function", "function": t.to_dict()} for t in tools
            ]

        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=self.model,
            messages=groq_messages,
            tools=groq_tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        duration_ms = (time.perf_counter() - t0) * 1000

        msg = resp.choices[0].message
        usage = resp.usage

        # Normalise tool calls
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {"raw": tc.function.arguments}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        stop = resp.choices[0].finish_reason or "stop"
        stop_map = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}

        return ProviderResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cost_usd=self.compute_cost(usage.prompt_tokens, usage.completion_tokens),
            duration_ms=duration_ms,
            model=self.model,
            stop_reason=stop_map.get(stop, stop),
            raw=resp,
        )
