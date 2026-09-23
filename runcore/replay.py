"""Counterfactual replay — "record once, test any change".

Every LLM call captured by RunCore (``import runcore.auto``, ``runcore.capture``)
stores the exact request it sent and the decision the model made. Replay re-sends
each recorded request, with the same context, to a *different* model and compares:

- decision: did it call the same tool with the same arguments? (strict)
- text: how close is the answer? (similarity, or an LLM judge if given)
- cost, tokens and latency, original vs new.

No agent code runs and no tool is executed — the recorded context already contains
the tool results the agent saw. That makes a replay free of side effects and cheap.

    runcore replay runcore_trace.json --model groq:llama-3.3-70b-versatile

For a full re-run of your own agent against recorded tool results, see
``recorded_tools()`` below.
"""
from __future__ import annotations

import difflib
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Targets: "provider:model"
# ---------------------------------------------------------------------------

_OPENAI_COMPATIBLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", ""),
}
PROVIDERS = sorted([*_OPENAI_COMPATIBLE, "anthropic"])


class ReplayError(RuntimeError):
    pass


def parse_target(target: str) -> tuple[str, str]:
    """'groq:llama-3.3-70b-versatile' -> ('groq', 'llama-3.3-70b-versatile')."""
    provider, _, model = target.partition(":")
    if not model or provider not in PROVIDERS:
        raise ReplayError(f"Target must be provider:model with provider in {PROVIDERS}, got {target!r}")
    return provider, model


# ---------------------------------------------------------------------------
# Format conversion. Canonical form = OpenAI chat format.
# ---------------------------------------------------------------------------

def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return "" if content is None else str(content)


def anthropic_to_openai(req: dict) -> dict:
    """Anthropic messages request -> OpenAI chat request (messages, tools, params)."""
    msgs: list[dict] = []
    if req.get("system"):
        msgs.append({"role": "system", "content": _text_of(req["system"])})
    for m in req.get("messages", []):
        content = m.get("content")
        if isinstance(content, str):
            msgs.append({"role": m["role"], "content": content})
            continue
        text = _text_of(content)
        calls = [{"id": b.get("id", ""), "type": "function",
                  "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))}}
                 for b in content or [] if b.get("type") == "tool_use"]
        results = [b for b in content or [] if b.get("type") == "tool_result"]
        for r in results:
            msgs.append({"role": "tool", "tool_call_id": r.get("tool_use_id", ""),
                         "content": _text_of(r.get("content"))})
        if calls:
            msgs.append({"role": "assistant", "content": text or None, "tool_calls": calls})
        elif text or not results:
            msgs.append({"role": m["role"], "content": text})
    out = {"messages": msgs}
    if req.get("tools"):
        out["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}})}}
            for t in req["tools"]]
    for k in ("temperature", "max_tokens", "top_p"):
        if k in req:
            out[k] = req[k]
    return out


def openai_to_anthropic(req: dict) -> dict:
    """OpenAI chat request -> Anthropic messages request."""
    system = "\n\n".join(_text_of(m.get("content")) for m in req["messages"] if m["role"] == "system")
    msgs: list[dict] = []

    def _add(role: str, blocks: list[dict]) -> None:
        if msgs and msgs[-1]["role"] == role:   # Anthropic needs alternating roles
            msgs[-1]["content"].extend(blocks)
        else:
            msgs.append({"role": role, "content": blocks})

    for m in req["messages"]:
        role = m["role"]
        if role == "system":
            continue
        if role == "tool":
            _add("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                           "content": _text_of(m.get("content"))}])
            continue
        blocks = [{"type": "text", "text": _text_of(m.get("content"))}] if _text_of(m.get("content")) else []
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments") or "{}"
            blocks.append({"type": "tool_use", "id": tc.get("id") or f"call_{len(blocks)}",
                           "name": fn.get("name", ""), "input": json.loads(args) if isinstance(args, str) else args})
        _add("assistant" if role == "assistant" else "user", blocks or [{"type": "text", "text": "."}])
    out: dict = {"messages": msgs, "max_tokens": req.get("max_tokens") or 1024}
    if system:
        out["system"] = system
    if req.get("tools"):
        out["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                         "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
                        for t in req["tools"]]
    for k in ("temperature", "top_p"):
        if k in req:
            out[k] = req[k]
    return out


def clean_messages(messages: list[dict]) -> list[dict]:
    """Keep only standard chat fields. SDK dumps add refusal/annotations/audio/index,
    which strict OpenAI-compatible APIs (Groq, Gemini) reject."""
    out = []
    for m in messages:
        c = {"role": m["role"], "content": m.get("content")}
        if m.get("tool_calls"):
            c["tool_calls"] = [{"id": tc.get("id", ""), "type": "function",
                                "function": {"name": tc["function"]["name"],
                                             "arguments": tc["function"].get("arguments") or "{}"}}
                               for tc in m["tool_calls"]]
            c["content"] = c["content"] or None
        for k in ("tool_call_id", "name"):
            if m.get(k):
                c[k] = m[k]
        out.append(c)
    return out


def _args(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except ValueError:
            return raw
    return raw or {}


def normalize_openai_output(resp: dict) -> dict:
    msg = (resp.get("choices") or [{}])[0].get("message") or {}
    return {"text": _text_of(msg.get("content")),
            "tool_calls": [{"name": tc["function"]["name"], "arguments": _args(tc["function"].get("arguments"))}
                           for tc in msg.get("tool_calls") or []]}


def normalize_anthropic_output(resp: dict) -> dict:
    blocks = resp.get("content") or []
    return {"text": _text_of(blocks),
            "tool_calls": [{"name": b["name"], "arguments": b.get("input", {})}
                           for b in blocks if b.get("type") == "tool_use"]}


# ---------------------------------------------------------------------------
# Calling a model
# ---------------------------------------------------------------------------

def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ReplayError(f"HTTP {e.code}: {e.read(300).decode(errors='replace')}") from None
    except urllib.error.URLError as e:
        raise ReplayError(f"Connection failed: {e.reason}") from None


def call_model(target: str, openai_req: dict, *, api_key: str | None = None,
               base_url: str | None = None, timeout: float = 60.0) -> dict:
    """Send a canonical (OpenAI-format) request to target. Returns output + usage + latency."""
    provider, model = parse_target(target)
    t0 = time.perf_counter()
    if provider == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ReplayError("ANTHROPIC_API_KEY not set")
        body = {**openai_to_anthropic(openai_req), "model": model}
        resp = _post((base_url or "https://api.anthropic.com/v1").rstrip("/") + "/messages", body,
                     {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        out = normalize_anthropic_output(resp)
        usage = resp.get("usage") or {}
        tin, tout = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    else:
        default_url, env = _OPENAI_COMPATIBLE[provider]
        key = api_key or (os.environ.get(env, "") if env else "")
        if env and not key:
            raise ReplayError(f"{env} not set")
        body = {k: v for k, v in openai_req.items() if k in ("tools", "tool_choice", "temperature", "max_tokens", "top_p")}
        body["messages"] = clean_messages(openai_req["messages"])
        body["model"] = model
        resp = _post((base_url or default_url).rstrip("/") + "/chat/completions", body,
                     {"Authorization": f"Bearer {key}"} if key else {}, timeout)
        out = normalize_openai_output(resp)
        usage = resp.get("usage") or {}
        tin, tout = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    return {**out, "input_tokens": tin, "output_tokens": tout,
            "latency_ms": (time.perf_counter() - t0) * 1000,
            "cost_usd": 0.0 if provider == "ollama" else model_cost(model, tin, tout)}


def model_cost(model: str, tin: int, tout: int) -> float | None:
    """None when the price is unknown — never pretend an unknown model is free."""
    from runcore.trace.tokens import MODEL_COSTS
    c = MODEL_COSTS.get(model)
    return None if c is None else tin * c["input"] + tout * c["output"]


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _canon_calls(calls: list[dict]) -> list[tuple[str, str]]:
    return sorted((c["name"], json.dumps(c.get("arguments"), sort_keys=True, default=str)) for c in calls)


def compare(orig: dict, new: dict) -> tuple[str, float]:
    """Return (status, similarity). Status: same | args_differ | diverged | similar | changed."""
    o, n = _canon_calls(orig.get("tool_calls", [])), _canon_calls(new.get("tool_calls", []))
    if o or n:
        if o == n:
            return "same", 1.0
        if [x[0] for x in o] == [x[0] for x in n]:
            return "args_differ", 0.5
        return "diverged", 0.0
    sim = difflib.SequenceMatcher(None, orig.get("text", ""), new.get("text", "")).ratio()
    return ("same" if sim >= 0.9 else "similar" if sim >= 0.5 else "changed"), round(sim, 3)


_JUDGE_PROMPT = (
    "You compare two answers an AI agent gave to the same conversation. Reply with exactly one word: "
    "EQUIVALENT if answer B would serve the user as well as answer A (same facts, same outcome), otherwise WORSE.\n\n"
    "Last user message:\n{question}\n\nAnswer A (original):\n{a}\n\nAnswer B (candidate):\n{b}"
)


def judge(judge_target: str, request: dict, a: str, b: str, **kw) -> bool:
    last_user = next((_text_of(m.get("content")) for m in reversed(request["messages"]) if m["role"] == "user"), "")
    out = call_model(judge_target, {"messages": [{"role": "user", "content": _JUDGE_PROMPT.format(
        question=last_user[:4000], a=a[:4000], b=b[:4000])}], "temperature": 0, "max_tokens": 5}, **kw)
    return out["text"].strip().upper().startswith("EQUIVALENT")


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

@dataclass
class ReplayStep:
    trace_id: str
    step: int
    original_model: str
    status: str                 # same | args_differ | diverged | similar | changed | equivalent | worse | error
    similarity: float = 0.0
    original: dict = field(default_factory=dict)
    candidate: dict = field(default_factory=dict)
    original_cost_usd: float = 0.0
    candidate_cost_usd: float | None = None
    original_latency_ms: float = 0.0
    candidate_latency_ms: float = 0.0
    original_input_tokens: int = 0
    candidate_input_tokens: int = 0
    error: str = ""


_OK = {"same", "similar", "equivalent"}
_BAD = {"args_differ", "diverged", "worse"}   # "changed" text without a judge = unverified, not worse


@dataclass
class ReplayReport:
    target: str
    steps: list[ReplayStep]
    skipped_spans: int = 0

    @property
    def compared(self) -> list[ReplayStep]:
        return [s for s in self.steps if s.status != "error"]

    def summary(self) -> dict:
        c = self.compared
        n = len(c)
        agree = sum(s.status in _OK for s in c)
        # A recorded $0 for a model with no known price means "unknown", not "free".
        orig_known = all(s.original_cost_usd or model_cost(s.original_model, 0, 0) is not None for s in c)
        orig_cost = sum(s.original_cost_usd for s in c) if orig_known else None
        known = [s.candidate_cost_usd for s in c if s.candidate_cost_usd is not None]
        new_cost = sum(known) if len(known) == n and n else None
        agreement = agree / n if n else 0.0
        regressions = sum(s.status in _BAD for s in c)
        unverified = sum(s.status == "changed" for s in c)
        # A gate: any changed tool decision needs a human look; >20% bad steps is a clear no.
        verdict = ("no_data" if not n else "unsafe" if regressions / n > 0.2
                   else "review" if regressions or unverified else "safe")
        return {
            "target": self.target,
            "steps": len(self.steps),
            "compared": n,
            "errors": len(self.steps) - n,
            "skipped_without_recording": self.skipped_spans,
            "agreement": round(agreement, 3),
            "regressions": regressions,
            "unverified_text": unverified,
            "tool_decisions_changed": sum(s.status in ("args_differ", "diverged") for s in c),
            "original_cost_usd": None if orig_cost is None else round(orig_cost, 6),
            "candidate_cost_usd": None if new_cost is None else round(new_cost, 6),
            "cost_change_pct": (None if new_cost is None or not orig_cost
                                else round((new_cost - orig_cost) / orig_cost * 100, 1)),
            "original_input_tokens": sum(s.original_input_tokens for s in c),
            "candidate_input_tokens": sum(s.candidate_input_tokens for s in c),
            "original_latency_ms": round(sum(s.original_latency_ms for s in c), 1),
            "candidate_latency_ms": round(sum(s.candidate_latency_ms for s in c), 1),
            "verdict": verdict,
        }

    def to_dict(self) -> dict:
        return {"summary": self.summary(), "steps": [asdict(s) for s in self.steps]}


def recorded_steps(trace: dict) -> Iterable[tuple[int, dict, dict]]:
    """Yield (index, llm_span, canonical_request) for every span with a recording."""
    for i, s in enumerate(trace.get("spans", [])):
        rec = (s.get("metadata") or {}).get("replay")
        if s.get("type") != "llm_call" or not rec:
            continue
        req = rec["request"]
        yield i, s, (anthropic_to_openai(req) if rec.get("format") == "anthropic" else req)


def replay(traces: Iterable[dict], target: str, *, judge_target: str | None = None,
           keys: dict[str, str] | None = None, base_url: str | None = None,
           max_steps: int | None = None, on_step=None) -> ReplayReport:
    """Replay every recorded LLM step of ``traces`` against ``target``.

    ``keys``: optional {provider: api_key} (overrides env vars; used by the Cloud).
    ``on_step``: optional callback(step: ReplayStep) for progress.
    """
    parse_target(target)
    keys = keys or {}

    def _kw(t: str) -> dict:
        return {"api_key": keys.get(parse_target(t)[0]), "base_url": base_url if t == target else None}

    steps: list[ReplayStep] = []
    skipped = 0
    for trace in traces:
        llm_spans = [s for s in trace.get("spans", []) if s.get("type") == "llm_call"]
        rec = list(recorded_steps(trace))
        skipped += len(llm_spans) - len(rec)
        for i, span, req in rec:
            if max_steps is not None and len(steps) >= max_steps:
                return ReplayReport(target, steps, skipped)
            orig = span["metadata"]["replay"]["output"]
            step = ReplayStep(trace_id=trace.get("trace_id", ""), step=i, original_model=span.get("model", ""),
                              status="error", original=orig, original_cost_usd=span.get("cost_usd", 0.0),
                              original_latency_ms=span.get("duration_ms", 0.0),
                              original_input_tokens=span.get("input_tokens", 0))
            try:
                new = call_model(target, req, **_kw(target))
                step.candidate = {"text": new["text"], "tool_calls": new["tool_calls"]}
                step.candidate_cost_usd = new["cost_usd"]
                step.candidate_latency_ms = new["latency_ms"]
                step.candidate_input_tokens = new["input_tokens"]
                step.status, step.similarity = compare(orig, new)
                if judge_target and step.status in ("similar", "changed"):
                    step.status = "equivalent" if judge(judge_target, req, orig.get("text", ""), new["text"],
                                                        **_kw(judge_target)) else "worse"
            except (ReplayError, KeyError, ValueError, TypeError) as exc:
                step.error = str(exc)[:300]
            steps.append(step)
            if on_step:
                on_step(step)
    return ReplayReport(target, steps, skipped)


# ---------------------------------------------------------------------------
# Full re-run: serve recorded tool results to @runcore.tool functions
# ---------------------------------------------------------------------------

class ReplayMiss(LookupError):
    """The agent called a tool with arguments that were never recorded."""


_recorded: dict[str, Any] | None = None
_on_miss = "raise"


def _tool_key(name: str, arguments: dict) -> str:
    return f"{name}:{json.dumps(arguments, sort_keys=True, default=str)}"


@contextmanager
def recorded_tools(trace: dict, on_miss: str = "raise"):
    """Re-run your agent (e.g. with a new model) against the tool results in ``trace``.

    Inside the block, every ``@runcore.tool`` function returns the recorded result for
    identical arguments instead of executing — no emails sent, no DB writes, no API bills.
    ``on_miss``: "raise" (ReplayMiss, default) or "call" (run the real tool).
    """
    global _recorded, _on_miss
    # ponytail: process-wide, one replay at a time; make it a ContextVar if replays run concurrently.
    table = {}
    for s in trace.get("spans", []):
        md = s.get("metadata") or {}
        if s.get("type") == "tool_call" and "result" in md:
            table[_tool_key(s["name"], s.get("arguments", {}))] = md["result"]
    prev = (_recorded, _on_miss)
    _recorded, _on_miss = table, on_miss
    try:
        yield table
    finally:
        _recorded, _on_miss = prev


def lookup_tool(name: str, arguments: dict) -> tuple[bool, Any]:
    """(hit, result). Called by @runcore.tool. (False, None) when no replay is active."""
    if _recorded is None:
        return False, None
    key = _tool_key(name, arguments)
    if key in _recorded:
        return True, _recorded[key]
    if _on_miss == "raise":
        raise ReplayMiss(f"{name}({arguments}) was not in the recording")
    return False, None


def load_traces(paths: Iterable[str]) -> list[dict]:
    """Load traces from .json (single / list / {"traces": [...]}) or .jsonl files and directories."""
    from pathlib import Path
    out: list[dict] = []
    for p in paths:
        path = Path(p)
        files = sorted(path.glob("*.json*")) if path.is_dir() else [path]
        for f in files:
            raw = f.read_text()
            try:
                data = json.loads(raw)
                items = data.get("traces", [data]) if isinstance(data, dict) else data
            except ValueError:
                items = [json.loads(line) for line in raw.splitlines() if line.strip()]
            out.extend(t for t in items if isinstance(t, dict) and "spans" in t)
    return out
