"""
ReplacementDetector — identify LLM tool calls that can be replaced by
deterministic Python code, and suggest the replacement.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from typing import Any

from runcore.core.models import AgentTrace, ToolCall
from runcore.replacement.patterns import DETERMINISTIC_PATTERNS

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Build fast lookup structures from DETERMINISTIC_PATTERNS
_TOOL_TO_PATTERN: dict[str, dict[str, Any]] = {}
for _p in DETERMINISTIC_PATTERNS:
    for _tool_name in _p["tool_names"]:
        _TOOL_TO_PATTERN[_tool_name.lower()] = _p

# Pattern types that are inherently deterministic (score high)
_HIGH_SCORE_TYPES = {"lookup", "format", "transform", "validate"}

# Argument-complexity heuristics:
# If a tool call has many arguments or very large argument values it is
# likely doing something more complex than a simple lookup / format.
_MAX_SIMPLE_ARGS = 5
_MAX_ARG_STR_LEN = 512

# Minimum call count before we consider a tool "repetitive"
_MIN_REPETITIONS_FOR_PATTERN = 2


def _argument_complexity_penalty(tool_calls: list[ToolCall]) -> float:
    """
    Return a penalty in [0, 0.4] based on average argument complexity.
    Simple, small arguments -> penalty near 0.
    Many / long arguments   -> penalty near 0.4.
    """
    if not tool_calls:
        return 0.0

    penalties = []
    for tc in tool_calls:
        arg_count = len(tc.arguments)
        arg_str   = str(tc.arguments)
        arg_len   = len(arg_str)

        p = 0.0
        if arg_count > _MAX_SIMPLE_ARGS:
            p += 0.2
        if arg_len > _MAX_ARG_STR_LEN:
            p += 0.2
        penalties.append(min(p, 0.4))

    return sum(penalties) / len(penalties)


def _repetition_score(tool_calls: list[ToolCall]) -> float:
    """
    Score in [0, 1] that reflects how repetitive the calls are.
    Identical (name, sorted-args) pairs score highest.
    """
    if not tool_calls:
        return 0.0

    signatures = [
        (tc.name, tuple(sorted(tc.arguments.keys())))
        for tc in tool_calls
    ]
    counts = Counter(signatures)
    max_count = max(counts.values())
    # Normalise: 1 call = 0, >= 5 identical calls = 1.0
    return min((max_count - 1) / 4.0, 1.0)


def _build_code_suggestion(tool_name: str, pattern: dict[str, Any], sample_calls: list[ToolCall]) -> str:
    """Wrap the pattern's code_template with call-site context."""
    sample_args = sample_calls[0].arguments if sample_calls else {}
    arg_repr    = repr(sample_args)

    header = textwrap.dedent(f"""\
        # -----------------------------------------------------------------------
        # Suggested replacement for tool: {tool_name!r}
        # Pattern : {pattern['name']}  ({pattern['pattern_type']})
        # Calls seen in trace: {len(sample_calls)}
        # Sample arguments   : {arg_repr[:200]}{"..." if len(arg_repr) > 200 else ""}
        # -----------------------------------------------------------------------

    """)
    return header + pattern["code_template"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ReplacementDetector:
    """
    Analyse an AgentTrace and identify tool calls that can be replaced by
    deterministic Python code.
    """

    # ------------------------------------------------------------------
    # Primary public methods
    # ------------------------------------------------------------------

    def analyze_trace(self, trace: AgentTrace) -> list[dict[str, Any]]:
        """
        Analyse every distinct tool in *trace* and return a list of findings.

        Each finding dict contains:
          - tool         (str)   : tool name
          - pattern      (str)   : matched pattern name, or "unknown"
          - replaceability_score (float): 0-1
          - suggestion   (str)   : Python code that could replace the calls,
                                   or an empty string when not replaceable
          - call_count   (int)   : how many times the tool was called
          - pattern_type (str | None): lookup / format / transform / validate
        """
        # Group calls by tool name
        by_tool: dict[str, list[ToolCall]] = {}
        for tc in trace.tool_calls:
            by_tool.setdefault(tc.name, []).append(tc)

        findings: list[dict[str, Any]] = []
        for tool_name, calls in by_tool.items():
            score       = self.score_replaceability(calls)
            pattern     = _TOOL_TO_PATTERN.get(tool_name.lower())
            suggestion  = ""
            pattern_name = "unknown"
            pattern_type = None

            if pattern and score >= 0.4:
                suggestion   = self.suggest_code_replacement(tool_name, calls)
                pattern_name = pattern["name"]
                pattern_type = pattern["pattern_type"]

            findings.append(
                {
                    "tool":                  tool_name,
                    "pattern":               pattern_name,
                    "replaceability_score":  round(score, 4),
                    "suggestion":            suggestion,
                    "call_count":            len(calls),
                    "pattern_type":          pattern_type,
                }
            )

        # Sort by replaceability descending (best candidates first)
        findings.sort(key=lambda d: d["replaceability_score"], reverse=True)
        return findings

    def score_replaceability(self, tool_calls: list[ToolCall]) -> float:
        """
        Return a float in [0, 1] estimating how easily the given tool calls
        can be replaced by deterministic code.

        Scoring logic
        -------------
        Base score:
          - 0.90  if all calls map to a known deterministic pattern
          - 0.50  otherwise (unknown tool, might still be replaceable)

        Modifiers (additive, capped to [0, 1]):
          +0.10  repetitive call signatures (same args structure used >= 2x)
          -0.40  tool has reasoning/judgment keywords in its name
                 (signals reasoning/judgment, not mechanical execution)
          -0.10  very low success rate (< 50% of calls succeeded)
          - arg-complexity penalty (0 - 0.40)

        Complex reasoning tools are intentionally scored < 0.3 so that they
        appear clearly non-replaceable in reports.
        """
        if not tool_calls:
            return 0.0

        tool_name_lower = tool_calls[0].name.lower()

        # --- Base score ---
        known = tool_name_lower in _TOOL_TO_PATTERN
        score = 0.90 if known else 0.50

        # --- Repetition bonus ---
        score += 0.10 * _repetition_score(tool_calls)

        # --- Reasoning / judgment penalty ---
        _REASONING_KEYWORDS = {
            "reason", "explain", "analyse", "analyze", "decide",
            "judge", "evaluate", "assess", "infer", "generate",
            "summarise", "summarize", "plan", "think", "interpret",
            "classify", "predict", "recommend",
        }
        name_tokens = set(tool_name_lower.replace("-", "_").split("_"))
        if name_tokens & _REASONING_KEYWORDS:
            score -= 0.40  # strong penalty — clearly needs LLM

        # --- Low-success penalty ---
        success_rate = sum(1 for tc in tool_calls if tc.success) / len(tool_calls)
        if success_rate < 0.5:
            score -= 0.10

        # --- Argument complexity penalty ---
        score -= _argument_complexity_penalty(tool_calls)

        return max(0.0, min(1.0, score))

    def detect_repetitive_patterns(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """
        Identify groups of tool calls that share the same name and argument
        structure, suggesting they are repetitive and replaceable.

        Returns a list of dicts, each with:
          - tool_name   (str)
          - count       (int)   total calls in the group
          - arg_keys    (tuple) the argument keys that were repeated
          - sample      (ToolCall) one representative call
          - score       (float) replaceability score for this group
        """
        groups: dict[tuple[str, tuple[str, ...]], list[ToolCall]] = {}
        for tc in tool_calls:
            key = (tc.name, tuple(sorted(tc.arguments.keys())))
            groups.setdefault(key, []).append(tc)

        results = []
        for (tool_name, arg_keys), calls in groups.items():
            if len(calls) < _MIN_REPETITIONS_FOR_PATTERN:
                continue
            results.append(
                {
                    "tool_name": tool_name,
                    "count":     len(calls),
                    "arg_keys":  arg_keys,
                    "sample":    calls[0],
                    "score":     self.score_replaceability(calls),
                }
            )

        results.sort(key=lambda d: d["count"], reverse=True)
        return results

    def suggest_code_replacement(
        self,
        tool_name: str,
        sample_calls: list[ToolCall],
    ) -> str:
        """
        Return a Python code string that could replace calls to *tool_name*.

        If the tool matches a known deterministic pattern the pattern's
        code_template is returned (with a contextual header).

        If the tool is unknown but calls are repetitive / simple, a generic
        lookup stub is generated.

        Returns an empty string when no useful suggestion can be made (e.g.
        the tool appears to require reasoning).
        """
        if not sample_calls:
            return ""

        score = self.score_replaceability(sample_calls)
        if score < 0.3:
            # Too complex / reasoning-heavy — do not suggest replacement
            return ""

        pattern = _TOOL_TO_PATTERN.get(tool_name.lower())
        if pattern:
            return _build_code_suggestion(tool_name, pattern, sample_calls)

        # Generic stub for unknown but simple tools
        sample_args  = sample_calls[0].arguments
        arg_names    = list(sample_args.keys())
        arg_list_str = ", ".join(f"{k}: object" for k in arg_names) if arg_names else ""
        return_hint  = repr(sample_calls[0].result) if sample_calls[0].result is not None else "None"

        return textwrap.dedent(f"""\
            # -----------------------------------------------------------------------
            # Generic replacement stub for tool: {tool_name!r}
            # (No exact pattern match — adapt as needed)
            # Replaceability score : {score:.2f}
            # Sample arguments     : {repr(sample_args)[:200]}
            # -----------------------------------------------------------------------

            def {tool_name.replace("-", "_")}({arg_list_str}) -> object:
                \"\"\"
                Deterministic replacement for the '{tool_name}' tool call.

                TODO: Implement the actual logic here.
                The tool was called {len(sample_calls)} time(s) in the trace.
                Sample return value: {return_hint!s:.200}
                \"\"\"
                raise NotImplementedError(
                    "Replace this stub with the actual deterministic implementation."
                )
        """)
