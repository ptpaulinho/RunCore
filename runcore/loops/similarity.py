"""Similarity utilities for comparing ToolCall instances."""

from __future__ import annotations

import hashlib
import json
from difflib import SequenceMatcher

from runcore.core.models import ToolCall


def compute_call_signature(tool_call: ToolCall) -> str:
    """Return a stable hash string of a tool call's name and arguments.

    The hash is deterministic: the same name and arguments always produce the
    same signature regardless of argument key ordering.
    """
    canonical = json.dumps(
        {"name": tool_call.name, "arguments": tool_call.arguments},
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def calls_are_identical(a: ToolCall, b: ToolCall) -> bool:
    """Return True when *a* and *b* have the same name and identical arguments."""
    return compute_call_signature(a) == compute_call_signature(b)


def calls_are_similar(a: ToolCall, b: ToolCall, threshold: float = 0.9) -> bool:
    """Return True when the similarity ratio between the two call signatures meets *threshold*.

    Similarity is measured using :class:`difflib.SequenceMatcher` on the JSON
    representations of each call's name and arguments.  A *threshold* of ``1.0``
    is equivalent to :func:`calls_are_identical`.
    """
    if a.name != b.name:
        return False

    sig_a = json.dumps({"name": a.name, "arguments": a.arguments}, sort_keys=True)
    sig_b = json.dumps({"name": b.name, "arguments": b.arguments}, sort_keys=True)

    ratio = SequenceMatcher(None, sig_a, sig_b).ratio()
    return ratio >= threshold
