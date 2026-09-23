"""Ollama provider adapter — 100% local, completely free.

Requires Ollama running locally: https://ollama.com
Install models:
  ollama pull llama3.2          # 2B, fast
  ollama pull llama3.1          # 8B, good quality
  ollama pull mistral           # 7B, strong tool use
  ollama pull gemma2:2b         # Google Gemma 2B, very fast
  ollama pull qwen2.5:7b        # Alibaba Qwen, excellent tool use

Ollama runs at http://localhost:11434 by default.
Override with env var: OLLAMA_HOST=http://your-host:11434
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from runcore.providers.base import BaseProvider, Message, ProviderResponse, ToolDefinition

_DEFAULT_MODEL = "llama3.2"
_DEFAULT_HOST = "http://localhost:11434"


class OllamaProvider(BaseProvider):
    name = "ollama"
    price_input_per_mtok = 0.0    # free — local compute only
    price_output_per_mtok = 0.0

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        host: str | None = None,
        api_key: str | None = None,  # unused, for interface compat
    ):
        super().__init__(model=model)
        self.model = model
        self._host = host or os.environ.get("OLLAMA_HOST", _DEFAULT_HOST)

    def is_available(self) -> bool:
        """Check if Ollama is running."""
        try:
            import urllib.request
            with urllib.request.urlopen(f"{self._host}/api/tags", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        try:
            import ollama as _ollama
        except ImportError as exc:
            raise ImportError("Install ollama: pip install ollama>=0.2.0") from exc

        client = _ollama.Client(host=self._host)

        # Convert messages
        ollama_messages = []
        for m in messages:
            if m.role == "tool":
                ollama_messages.append({
                    "role": "tool",
                    "content": m.content,
                })
            elif m.tool_calls:
                # Normalise to Ollama's expected format: arguments must be a dict
                norm_calls = []
                for tc in m.tool_calls:
                    fn = tc.get("function", tc)
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            import json as _json
                            args = _json.loads(args)
                        except Exception:
                            args = {}
                    norm_calls.append({
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {"name": fn.get("name", tc.get("name", "")), "arguments": args},
                    })
                ollama_messages.append({
                    "role": "assistant",
                    "content": m.content or "",
                    "tool_calls": norm_calls,
                })
            else:
                ollama_messages.append({"role": m.role, "content": m.content})

        # Convert tools
        ollama_tools = None
        if tools:
            ollama_tools = [
                {"type": "function", "function": t.to_dict()} for t in tools
            ]

        t0 = time.perf_counter()
        resp = client.chat(
            model=self.model,
            messages=ollama_messages,
            tools=ollama_tools,
            options={"num_predict": max_tokens, "temperature": temperature},
        )
        duration_ms = (time.perf_counter() - t0) * 1000

        msg = resp.message
        usage = getattr(resp, "usage", None) or {}

        # Extract token counts
        if hasattr(resp, "prompt_eval_count"):
            input_tokens = resp.prompt_eval_count or 0
            output_tokens = resp.eval_count or 0
        elif isinstance(usage, dict):
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
        else:
            # Estimate
            input_tokens = sum(len(m.content.split()) * 4 // 3 for m in messages)
            output_tokens = len((msg.content or "").split()) * 4 // 3

        # Normalise tool calls
        tool_calls = []
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                fn = tc.function if hasattr(tc, "function") else tc
                try:
                    args = fn.arguments if isinstance(fn.arguments, dict) else json.loads(fn.arguments)
                except Exception:
                    args = {}
                tool_calls.append({
                    "id": f"call_{fn.name}_{int(t0*1000)}",
                    "name": fn.name,
                    "arguments": args,
                })

        stop_reason = "tool_use" if tool_calls else "end_turn"

        return ProviderResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,
            duration_ms=duration_ms,
            model=self.model,
            stop_reason=stop_reason,
            raw=resp,
        )
