"""instrument() decorator and auto_instrument() monkey-patcher."""
from __future__ import annotations

import functools
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from runcore.sdk import context as _ctx
from runcore.sdk.capture import Capture


def instrument(target=None, *, agent_name: str | None = None, task: str = "", framework: str = "unknown"):
    """Wrap a callable so every call automatically creates a Capture context.

    Works as a decorator or as a plain wrapper::

        @runcore.instrument
        def run_agent(prompt):
            ...

        # or with options:
        @runcore.instrument(agent_name="my_agent", framework="langchain")
        def run_agent(prompt):
            ...

        # or inline:
        traced_fn = runcore.instrument(existing_fn, agent_name="my_agent")
    """
    def _decorate(fn: Callable) -> Callable:
        name = agent_name or getattr(fn, "__name__", "agent")

        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            cap = Capture(agent_name=name, task=task, framework=framework)
            with cap:
                try:
                    result = fn(*args, **kwargs)
                    return result
                except Exception:
                    cap.set_success(False)
                    raise

        _wrapper.__runcore_capture__ = True
        _wrapper.__runcore_unwrapped__ = fn
        return _wrapper

    if target is not None:
        # Called as @instrument or instrument(fn) without parens
        if callable(target):
            return _decorate(target)
        raise TypeError(f"instrument() target must be callable, got {type(target)}")

    # Called as @instrument(...) with keyword args
    return _decorate


def auto_instrument(frameworks: list[str] | None = None) -> dict[str, bool]:
    """Monkey-patch LLM clients so calls inside any ``Capture`` context are recorded.

    When ``frameworks`` is None, patches all available clients.

    Returns a dict mapping client name → patched successfully.

    Example::

        runcore.auto_instrument()   # patches anthropic + openai if installed

        with runcore.capture("my_agent") as c:
            response = anthropic.Anthropic().messages.create(...)
            # ↑ automatically captured in c

        trace = c.get_atir()
    """
    from runcore.sdk.proxy import patch_all, _patch_anthropic, _patch_openai

    if frameworks is None:
        return patch_all()

    result: dict[str, bool] = {}
    for fw in frameworks:
        if fw == "anthropic":
            result["anthropic"] = _patch_anthropic()
        elif fw == "openai":
            result["openai"] = _patch_openai()
        else:
            result[fw] = False
    return result


def uninstrument() -> None:
    """Restore all original LLM client methods patched by auto_instrument()."""
    from runcore.sdk.proxy import unpatch_all
    unpatch_all()


def instrument_object(obj: Any, method_name: str = "run", **kwargs) -> Any:
    """Wrap a method on an existing object instance.

    Useful for frameworks that create agent objects::

        agent = MyLangChainAgent()
        runcore.instrument_object(agent, method_name="run", agent_name="lc_agent")
        agent.run("do something")  # now captured automatically
    """
    original = getattr(obj, method_name)
    wrapped = instrument(original, **kwargs)
    setattr(obj, method_name, wrapped)
    return obj
