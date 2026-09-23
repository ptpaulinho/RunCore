"""Verified fixes — find cheaper configurations and *prove* them before you ship.

RunCore proposes concrete changes, replays your recorded runs with each change
applied (runcore.replay), and keeps only the ones that hold up:

- switch_model:  a cheaper/faster model that makes the same decisions.
- prune_tools:   tools offered on every call but never used — dropping them cuts
                 input tokens on every request.

Verified fixes are written to a policy file (``runcore.fix.json``). With
``import runcore.auto`` / ``runcore.auto_instrument()`` the policy is applied at
runtime — no code change:

    runcore fix runcore_trace.json --current openai:gpt-4o --try openai:gpt-4o-mini
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from runcore import replay as rp


@dataclass
class Fix:
    kind: str                      # switch_model | prune_tools
    title: str
    change: dict
    verified: bool
    verdict: str
    agreement: float
    cost_change_pct: float | None
    input_tokens_change_pct: float | None
    steps: int
    summary: dict = field(default_factory=dict)


@dataclass
class FixReport:
    current: str
    fixes: list[Fix]

    def verified(self) -> list[Fix]:
        return [f for f in self.fixes if f.verified]

    def policy(self) -> dict:
        """Runtime policy from verified fixes. Model switches only within the same provider,
        because your code's client talks to one provider."""
        cur_provider, cur_model = rp.parse_target(self.current)
        pol: dict[str, Any] = {"version": 1, "source": "runcore fix", "fixes": []}
        best = next((f for f in self.verified() if f.kind == "switch_model"
                     and rp.parse_target(f.change["to"])[0] == cur_provider), None)
        if best:
            pol["model_map"] = {cur_model: rp.parse_target(best.change["to"])[1]}
            pol["fixes"].append(best.title)
        prune = next((f for f in self.verified() if f.kind == "prune_tools"), None)
        if prune:
            pol["drop_tools"] = prune.change["drop_tools"]
            pol["fixes"].append(prune.title)
        return pol

    def to_dict(self) -> dict:
        return {"current": self.current, "fixes": [asdict(f) for f in self.fixes], "policy": self.policy()}

    def markdown(self) -> str:
        """PR/CI comment."""
        lines = [f"### RunCore verified fixes (baseline `{self.current}`)", "",
                 "| Fix | Verified | Agreement | Cost | Input tokens |", "|---|---|---|---|---|"]
        for f in self.fixes:
            pct = lambda v: "n/a" if v is None else f"{v:+.1f}%"  # noqa: E731
            lines.append(f"| {f.title} | {'✅' if f.verified else '❌ ' + f.verdict} | "
                         f"{f.agreement * 100:.0f}% of {f.steps} | {pct(f.cost_change_pct)} | "
                         f"{pct(f.input_tokens_change_pct)} |")
        pol = self.policy()
        if pol.get("fixes"):
            lines += ["", "Apply with `runcore.fix.json` (loaded automatically by `import runcore.auto`):",
                      "```json", json.dumps(pol, indent=2), "```"]
        else:
            lines += ["", "No change passed verification — current configuration kept."]
        return "\n".join(lines)


def _pct(new: float, old: float) -> float | None:
    return None if not old else round((new - old) / old * 100, 1)


def _as_fix(kind: str, title: str, change: dict, report: rp.ReplayReport) -> Fix:
    sm = report.summary()
    return Fix(kind=kind, title=title, change=change, verified=sm["verdict"] == "safe", verdict=sm["verdict"],
               agreement=sm["agreement"], cost_change_pct=sm["cost_change_pct"],
               # Across models, token counts differ by tokenizer — only meaningful for the same model.
               input_tokens_change_pct=(None if kind == "switch_model"
                                        else _pct(sm["candidate_input_tokens"], sm["original_input_tokens"])),
               steps=sm["compared"], summary=sm)


def unused_tools(traces: list[dict]) -> list[str]:
    offered, used = set(), set()
    for t in traces:
        for _, span, req in rp.recorded_steps(t):
            offered |= {tool["function"]["name"] for tool in req.get("tools") or []}
            used |= {c["name"] for c in span["metadata"]["replay"]["output"].get("tool_calls", [])}
    return sorted(offered - used)


def _without_tools(traces: list[dict], drop: set[str]) -> list[dict]:
    out = copy.deepcopy(traces)
    for t in out:
        for s in t.get("spans", []):
            rec = (s.get("metadata") or {}).get("replay")
            if not rec or not rec["request"].get("tools"):
                continue
            name_of = (lambda x: x["name"]) if rec.get("format") == "anthropic" else (lambda x: x["function"]["name"])
            rec["request"]["tools"] = [x for x in rec["request"]["tools"] if name_of(x) not in drop]
            if not rec["request"]["tools"]:
                rec["request"].pop("tools")
                rec["request"].pop("tool_choice", None)
    return out


def find_fixes(traces: list[dict], current: str, candidates: list[str] = (), *, judge_target: str | None = None,
               keys: dict | None = None, base_url: str | None = None, max_steps: int | None = None) -> FixReport:
    """Try each candidate model and tool pruning; verify every change by replay."""
    rp.parse_target(current)
    kw = dict(judge_target=judge_target, keys=keys, max_steps=max_steps)
    fixes: list[Fix] = []
    for cand in candidates:
        if cand == current:
            continue
        rep = rp.replay(traces, cand, base_url=base_url, **kw)
        fixes.append(_as_fix("switch_model", f"Switch {current} → {cand}", {"from": current, "to": cand}, rep))
    drop = unused_tools(traces)
    if drop:
        rep = rp.replay(_without_tools(traces, set(drop)), current, base_url=base_url, **kw)
        fixes.append(_as_fix("prune_tools", f"Drop never-used tools: {', '.join(drop)}", {"drop_tools": drop}, rep))

    def _rank(f: Fix):
        saving = f.cost_change_pct if f.cost_change_pct is not None else (f.input_tokens_change_pct or 0)
        return (not f.verified, saving)
    return FixReport(current, sorted(fixes, key=_rank))


# ---------------------------------------------------------------------------
# Runtime: apply a policy to outgoing requests (used by sdk.proxy)
# ---------------------------------------------------------------------------

_policy: dict | None = None
_policy_loaded = False


def load_policy() -> dict:
    """RUNCORE_POLICY=<path>, else ./runcore.fix.json if present. Cached."""
    global _policy, _policy_loaded
    if not _policy_loaded:
        path = Path(os.environ.get("RUNCORE_POLICY", "runcore.fix.json"))
        try:
            _policy = json.loads(path.read_text()) if path.is_file() else {}
        except (OSError, ValueError):
            _policy = {}
        _policy_loaded = True
    return _policy or {}


def reset_policy() -> None:
    global _policy, _policy_loaded
    _policy, _policy_loaded = None, False


def apply_policy(fmt: str, kwargs: dict) -> tuple[dict, list[str]]:
    """Return (new kwargs, applied fix names). Never mutates the caller's dict."""
    pol = load_policy()
    if not pol:
        return kwargs, []
    applied = []
    new = dict(kwargs)
    mapped = (pol.get("model_map") or {}).get(new.get("model"))
    if mapped:
        new["model"] = mapped
        applied.append(f"model:{kwargs['model']}->{mapped}")
    drop = set(pol.get("drop_tools") or [])
    if drop and new.get("tools"):
        name_of = (lambda x: x.get("name")) if fmt == "anthropic" else (lambda x: (x.get("function") or {}).get("name"))
        kept = [t for t in new["tools"] if name_of(t) not in drop]
        if len(kept) != len(new["tools"]):
            applied.append(f"drop_tools:{len(new['tools']) - len(kept)}")
            new["tools"] = kept
            if not kept:  # APIs reject an empty tools list
                new.pop("tools")
                new.pop("tool_choice", None)
    return new, applied
