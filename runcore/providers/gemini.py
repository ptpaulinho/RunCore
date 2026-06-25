"""Google Gemini provider adapter — generous free tier.

Free tier (as of 2026):
  gemini-1.5-flash-8b  — fastest, very cheap
  gemini-1.5-flash     — better quality, still free tier
  gemini-2.0-flash-exp — latest, experimental

Get a free API key: https://aistudio.google.com/apikey
Set env var: GEMINI_API_KEY=AIza...
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from runcore.providers.base import BaseProvider, Message, ProviderResponse, ToolDefinition

_DEFAULT_MODEL = "gemini-1.5-flash-8b"


class GeminiProvider(BaseProvider):
    name = "gemini"
    # Free tier: 0 cost; paid tier shown below (approx)
    price_input_per_mtok = 0.0
    price_output_per_mtok = 0.0

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
    ):
        super().__init__(model=model, api_key=api_key)
        self.model = model
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def is_available(self) -> bool:
        if not self._api_key:
            return False
        try:
            import google.generativeai  # noqa: F401
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
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "Install google-generativeai: pip install google-generativeai>=0.7.0"
            ) from exc

        genai.configure(api_key=self._api_key)

        # Build Gemini tool declarations
        gemini_tools = None
        if tools:
            from google.generativeai.types import FunctionDeclaration, Tool
            declarations = [
                FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters=t.parameters,
                )
                for t in tools
            ]
            gemini_tools = [Tool(function_declarations=declarations)]

        model = genai.GenerativeModel(
            model_name=self.model,
            tools=gemini_tools,
            generation_config={"max_output_tokens": max_tokens, "temperature": temperature},
        )

        # Convert to Gemini format
        # Gemini uses a different history format — system as first user message
        history = []
        system_content = None
        gemini_messages = []

        for m in messages:
            if m.role == "system":
                system_content = m.content
            elif m.role == "user":
                gemini_messages.append({"role": "user", "parts": [m.content]})
            elif m.role == "assistant":
                parts = []
                if m.content:
                    parts.append(m.content)
                if m.tool_calls:
                    for tc in m.tool_calls:
                        from google.generativeai.types import FunctionCall
                        parts.append({"function_call": {"name": tc["name"], "args": tc.get("arguments", {})}})
                gemini_messages.append({"role": "model", "parts": parts})
            elif m.role == "tool":
                gemini_messages.append({
                    "role": "user",
                    "parts": [{"function_response": {"name": "tool", "response": {"result": m.content}}}],
                })

        if system_content and gemini_messages:
            # Prepend system prompt to first user message
            first = gemini_messages[0]
            if isinstance(first["parts"][0], str):
                first["parts"][0] = f"{system_content}\n\n{first['parts'][0]}"

        chat = model.start_chat(history=gemini_messages[:-1] if len(gemini_messages) > 1 else [])

        t0 = time.perf_counter()
        last_msg = gemini_messages[-1]["parts"] if gemini_messages else ["Hello"]
        response = chat.send_message(last_msg if isinstance(last_msg, list) else [last_msg])
        duration_ms = (time.perf_counter() - t0) * 1000

        # Parse response
        content = ""
        tool_calls = []
        if response.candidates:
            candidate = response.candidates[0]
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    content += part.text
                if hasattr(part, "function_call") and part.function_call.name:
                    fc = part.function_call
                    tool_calls.append({
                        "id": f"call_{fc.name}_{int(t0*1000)}",
                        "name": fc.name,
                        "arguments": dict(fc.args) if fc.args else {},
                    })

        # Token counts (approximate — Gemini doesn't always return them)
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        stop_reason = "end_turn" if not tool_calls else "tool_use"

        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self.compute_cost(input_tokens, output_tokens),
            duration_ms=duration_ms,
            model=self.model,
            stop_reason=stop_reason,
            raw=response,
        )
