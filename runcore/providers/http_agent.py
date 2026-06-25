"""HTTP agent provider — certify a company's OWN agent over an HTTP endpoint.

This is the "bring your own agent" path: instead of certifying a generic model
(Groq, Ollama), a company points RunCore at *their* deployment and we drive the
benchmark tasks through it, measuring tokens / cost / task success exactly the
same way as for any other provider.

The endpoint must be **OpenAI chat-completions compatible** — i.e. accept

    POST {base_url}/chat/completions
    { "model": ..., "messages": [...], "tools": [...], ... }

and return the standard ``choices[0].message`` shape (with ``tool_calls`` when the
agent decides to call a tool). The vast majority of self-hosted/proxied agents
(vLLM, LiteLLM, Ollama's OpenAI shim, custom FastAPI gateways, OpenRouter, …)
speak this protocol, so a company can certify their real production agent without
running anything in a terminal.

Token usage is read from the response ``usage`` block when present; otherwise it
is estimated with tiktoken so cost/efficiency dimensions still have signal.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from runcore.providers.base import BaseProvider, Message, ProviderResponse, ToolDefinition


class HttpAgentProvider(BaseProvider):
    """Drives an external OpenAI-compatible agent endpoint."""

    name = "http"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str = "agent",
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        timeout: float = 120.0,
        price_input_per_mtok: float = 0.0,
        price_output_per_mtok: float = 0.0,
        **kwargs: Any,
    ):
        super().__init__(model=model, api_key=api_key)
        self.model = model
        self.base_url = (base_url or os.environ.get("RUNCORE_AGENT_URL", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("RUNCORE_AGENT_KEY", "")
        self.auth_header = auth_header
        self.auth_scheme = auth_scheme
        self.timeout = timeout
        self.price_input_per_mtok = price_input_per_mtok
        self.price_output_per_mtok = price_output_per_mtok

    # ------------------------------------------------------------------ helpers
    def _endpoint(self) -> str:
        # Allow the user to pass either the base ("…/v1") or the full path.
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            scheme = f"{self.auth_scheme} " if self.auth_scheme else ""
            headers[self.auth_header] = f"{scheme}{self._api_key}"
        return headers

    def is_available(self) -> bool:
        if not self.base_url:
            return False
        try:
            import httpx  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            # ~4 chars per token fallback
            return max(1, len(text) // 4)

    # ------------------------------------------------------------------- chat
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        try:
            import httpx
        except ImportError as exc:
            raise ImportError("Install httpx: pip install httpx>=0.27.0") from exc

        if not self.base_url:
            raise ValueError("HttpAgentProvider requires a base_url (the company's agent endpoint).")

        oa_messages = []
        for m in messages:
            if m.role == "tool":
                oa_messages.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                })
            elif m.tool_calls:
                oa_messages.append({
                    "role": "assistant",
                    "content": m.content or "",
                    "tool_calls": m.tool_calls,
                })
            else:
                oa_messages.append({"role": m.role, "content": m.content})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": oa_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t.to_dict()} for t in tools]

        t0 = time.perf_counter()
        resp = httpx.post(self._endpoint(), json=payload, headers=self._headers(), timeout=self.timeout)
        duration_ms = (time.perf_counter() - t0) * 1000
        resp.raise_for_status()
        data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        content = msg.get("content") or ""

        # Normalise tool calls -> [{id, name, arguments: dict}]
        tool_calls = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", tc)
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"raw": args}
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": args,
            })

        usage = data.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens") or 0)
        out_tok = int(usage.get("completion_tokens") or 0)
        if not in_tok:
            in_tok = sum(self._estimate_tokens(m.get("content") or "") for m in oa_messages)
        if not out_tok:
            out_tok = self._estimate_tokens(content) + sum(
                self._estimate_tokens(json.dumps(tc["arguments"])) for tc in tool_calls
            )

        finish = choice.get("finish_reason") or "stop"
        stop_map = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}

        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self.compute_cost(in_tok, out_tok),
            duration_ms=duration_ms,
            model=self.model,
            stop_reason=stop_map.get(finish, finish),
            raw=data,
        )
