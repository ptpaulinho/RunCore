"""``@runcore.tool`` — decorator that makes an arbitrary tool function dedup-aware.

The dedup/context guards can only cut waste on tool calls RunCore can see. For a
customer's own agent, that means their tool-dispatch loop has to tell RunCore when
a tool runs. Calling ``cap.dedup_check()``/``cap.record_tool()`` by hand around every
tool is the minimal integration — this decorator is that integration, pre-written,
so wrapping a tool function is a one-line decorator instead of a few lines of
boilerplate per call site.

Usage::

    @runcore.tool
    def get_weather(city: str) -> dict:
        return call_weather_api(city)

    with runcore.capture("my_agent", guards=runcore.GuardConfig()) as cap:
        get_weather("Lisbon")
        get_weather("Lisbon")   # <- duplicate: skipped, cached result returned instantly

Outside an active ``capture()`` block the decorator is a no-op passthrough — safe to
leave applied everywhere, always.
"""
from __future__ import annotations

import functools
import inspect
import json
import time
from typing import Any, Callable

from runcore.sdk import context as _ctx


def _signature(arguments: dict[str, Any]) -> str:
    """Stable cache key for a call's bound arguments."""
    try:
        return json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(arguments)


def _bind_arguments(fn: Callable, args: tuple, kwargs: dict) -> dict[str, Any]:
    """Normalize positional+keyword call args into a stable {name: value} dict.

    Falls back to a raw wrapper when the signature can't be bound (e.g. *args-only
    functions) so dedup still works, just keyed less precisely.
    """
    try:
        bound = inspect.signature(fn).bind(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except (TypeError, ValueError):
        return {"_args": list(args), "_kwargs": kwargs}


def tool(fn: Callable | None = None, *, name: str | None = None) -> Callable:
    """Decorate a tool function so it auto-registers with the active Capture's guards.

    Can be used bare (``@runcore.tool``) or with a custom name (``@runcore.tool(name=...)``).
    """

    def _decorate(target: Callable) -> Callable:
        tool_name = name or target.__name__

        @functools.wraps(target)
        def _wrapper(*args, **kwargs):
            cap = _ctx.current()
            if cap is None:
                return target(*args, **kwargs)

            # Cache lives on the Capture instance, not this closure — scoped to one
            # agent run so it never leaks across requests/tenants and is freed with
            # the capture. Tolerate older Capture instances that predate this cache.
            cache: dict[str, tuple[Any, bool]] = getattr(cap, "_tool_result_cache", None)
            if cache is None:
                cache = {}
                cap._tool_result_cache = cache

            arguments = _bind_arguments(target, args, kwargs)
            cache_key = f"{tool_name}:{_signature(arguments)}"

            if cap.dedup_check(tool_name, arguments) and cache_key in cache:
                cached_result, cached_ok = cache[cache_key]
                cap.record_tool(
                    tool_name, arguments, cached_result, cached_ok,
                    duration_ms=0.0, metadata={"deduplicated": True}, skip_guard=True,
                )
                return cached_result

            t0 = time.perf_counter()
            try:
                result = target(*args, **kwargs)
                ok = True
            except Exception as exc:
                duration_ms = (time.perf_counter() - t0) * 1000
                cap.record_tool(tool_name, arguments, {"error": str(exc)}, False,
                                duration_ms, skip_guard=True)
                cache[cache_key] = ({"error": str(exc)}, False)
                raise
            duration_ms = (time.perf_counter() - t0) * 1000
            cap.record_tool(tool_name, arguments, result, ok, duration_ms, skip_guard=True)
            cache[cache_key] = (result, ok)
            return result

        _wrapper.__runcore_tool__ = True
        _wrapper.__runcore_unwrapped__ = target
        return _wrapper

    if fn is not None:
        return _decorate(fn)
    return _decorate
