"""Thread-local capture context stack — shared state for the SDK."""
from __future__ import annotations

import threading

_local = threading.local()


def push(capture) -> None:
    if not hasattr(_local, "stack"):
        _local.stack = []
    _local.stack.append(capture)


def pop() -> None:
    if hasattr(_local, "stack") and _local.stack:
        _local.stack.pop()


def current():
    """Return the innermost active capture, or None."""
    stack = getattr(_local, "stack", None)
    return stack[-1] if stack else None


def is_active() -> bool:
    return current() is not None
