"""LLM client proxies — monkey-patch Anthropic and OpenAI to auto-capture calls."""
from __future__ import annotations

import time
from typing import Any

from runcore.sdk import context as _ctx

_anthropic_patched = False
_openai_patched = False
_original_anthropic_create = None
_original_openai_create = None


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

# Recording the request + decision is what makes a trace replayable (runcore.replay).
# Opt out with RUNCORE_RECORD_CONTENT=0 (e.g. prompts contain data you must not store).
_REPLAY_KEYS = ("messages", "system", "tools", "tool_choice", "temperature", "max_tokens", "top_p")
_MAX_RECORDING_BYTES = 512_000


def _recording(fmt: str, kwargs: dict, response) -> dict:
    import json
    import os
    if os.environ.get("RUNCORE_RECORD_CONTENT", "1") == "0":
        return {}
    from runcore.replay import normalize_anthropic_output, normalize_openai_output
    try:
        dump = lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)  # noqa: E731
        req = json.loads(json.dumps({k: kwargs[k] for k in _REPLAY_KEYS if k in kwargs}, default=dump))
        raw = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        out = normalize_anthropic_output(raw) if fmt == "anthropic" else normalize_openai_output(raw)
    except Exception:
        return {}
    if len(json.dumps(req)) > _MAX_RECORDING_BYTES:
        return {"replay_skipped": "request_too_large"}
    return {"replay": {"format": fmt, "request": req, "output": out}}


def _patch_anthropic() -> bool:
    global _anthropic_patched, _original_anthropic_create
    if _anthropic_patched:
        return True
    try:
        import anthropic
        from runcore.trace.cost import calculate_llm_cost

        # The create method lives on the Messages resource class
        target = anthropic.resources.messages.Messages
        _original_anthropic_create = target.create

        def _patched_create(self_inner, *args, **kwargs):
            from runcore.fix import apply_policy
            kwargs, applied = apply_policy("anthropic", kwargs)  # verified fixes from runcore.fix.json
            capture = _ctx.current()
            if capture is None:
                return _original_anthropic_create(self_inner, *args, **kwargs)

            t0 = time.perf_counter()
            response = _original_anthropic_create(self_inner, *args, **kwargs)
            elapsed = (time.perf_counter() - t0) * 1000

            usage = getattr(response, "usage", None)
            input_tok = getattr(usage, "input_tokens", 0) if usage else 0
            output_tok = getattr(usage, "output_tokens", 0) if usage else 0
            model = getattr(response, "model", kwargs.get("model", "claude-3-5-sonnet-20241022"))
            cost = calculate_llm_cost(model, input_tok, output_tok)

            messages = kwargs.get("messages", [])
            tools = kwargs.get("tools", [])
            stop_reason = getattr(response, "stop_reason", None)

            capture.record_llm(
                provider="anthropic",
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cost_usd=cost,
                duration_ms=elapsed,
                stop_reason=str(stop_reason) if stop_reason else None,
                messages_count=len(messages),
                tools_count=len(tools),
                metadata={**_recording("anthropic", kwargs, response), **({"policy_applied": applied} if applied else {})},
            )
            # Auto-check loop risk after each LLM call
            try:
                from runcore.loops.detector import LoopDetector
                score = LoopDetector().calculate_loop_risk_score(capture.collector.build_trace())
                capture.check_loop_risk(score)
            except Exception:
                pass
            return response

        target.create = _patched_create
        _anthropic_patched = True
        return True
    except (ImportError, AttributeError):
        return False


def _unpatch_anthropic() -> None:
    global _anthropic_patched, _original_anthropic_create
    if not _anthropic_patched or _original_anthropic_create is None:
        return
    try:
        import anthropic
        anthropic.resources.messages.Messages.create = _original_anthropic_create
        _anthropic_patched = False
        _original_anthropic_create = None
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

def _patch_openai() -> bool:
    global _openai_patched, _original_openai_create
    if _openai_patched:
        return True
    try:
        import openai
        from runcore.trace.cost import calculate_llm_cost

        target = openai.resources.chat.completions.Completions
        _original_openai_create = target.create

        def _patched_create(self_inner, *args, **kwargs):
            from runcore.fix import apply_policy
            kwargs, applied = apply_policy("openai", kwargs)  # verified fixes from runcore.fix.json
            capture = _ctx.current()
            if capture is None:
                return _original_openai_create(self_inner, *args, **kwargs)

            t0 = time.perf_counter()
            response = _original_openai_create(self_inner, *args, **kwargs)
            elapsed = (time.perf_counter() - t0) * 1000

            usage = getattr(response, "usage", None)
            input_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
            output_tok = getattr(usage, "completion_tokens", 0) if usage else 0
            model = getattr(response, "model", kwargs.get("model", "gpt-4"))
            cost = calculate_llm_cost(model, input_tok, output_tok)

            messages = kwargs.get("messages", [])
            tools = kwargs.get("tools", [])
            choices = getattr(response, "choices", [])
            stop_reason = choices[0].finish_reason if choices else None

            capture.record_llm(
                provider="openai",
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cost_usd=cost,
                duration_ms=elapsed,
                stop_reason=str(stop_reason) if stop_reason else None,
                messages_count=len(messages),
                tools_count=len(tools),
                metadata={**_recording("openai", kwargs, response), **({"policy_applied": applied} if applied else {})},
            )
            try:
                from runcore.loops.detector import LoopDetector
                score = LoopDetector().calculate_loop_risk_score(capture.collector.build_trace())
                capture.check_loop_risk(score)
            except Exception:
                pass
            return response

        target.create = _patched_create
        _openai_patched = True
        return True
    except (ImportError, AttributeError):
        return False


def _unpatch_openai() -> None:
    global _openai_patched, _original_openai_create
    if not _openai_patched or _original_openai_create is None:
        return
    try:
        import openai
        openai.resources.chat.completions.Completions.create = _original_openai_create
        _openai_patched = False
        _original_openai_create = None
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def patch_all() -> dict[str, bool]:
    """Patch all available LLM clients. Returns which were successfully patched."""
    return {
        "anthropic": _patch_anthropic(),
        "openai": _patch_openai(),
    }


def unpatch_all() -> None:
    """Restore all original LLM client methods."""
    _unpatch_anthropic()
    _unpatch_openai()


def is_patched() -> dict[str, bool]:
    return {"anthropic": _anthropic_patched, "openai": _openai_patched}
