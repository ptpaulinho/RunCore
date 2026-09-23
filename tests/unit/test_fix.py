"""Verified fixes: candidate search, verification by replay, runtime policy."""
import json

import pytest

from runcore import fix as fx
from runcore import replay as rp


def _tool(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def _trace():
    req = {"messages": [{"role": "user", "content": "Refund INV-1"}],
           "tools": [_tool("refund"), _tool("cancel_subscription"), _tool("send_marketing_email")]}
    out = {"text": "", "tool_calls": [{"name": "refund", "arguments": {"invoice": "INV-1"}}]}
    span = {"type": "llm_call", "model": "gpt-4o", "cost_usd": 0.002, "duration_ms": 800.0, "input_tokens": 300,
            "metadata": {"replay": {"format": "openai", "request": req, "output": out}}}
    return {"trace_id": "t", "spans": [span]}


def _reply(tool="refund", args=None, prompt_tokens=100):
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c", "function": {"name": tool, "arguments": json.dumps(args or {"invoice": "INV-1"})}}]}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 10}}


@pytest.fixture(autouse=True)
def _no_policy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RUNCORE_POLICY", raising=False)
    fx.reset_policy()
    yield
    fx.reset_policy()


def test_unused_tools():
    assert fx.unused_tools([_trace()]) == ["cancel_subscription", "send_marketing_email"]


def test_find_fixes_verifies_and_ranks(monkeypatch):
    def fake_post(url, body, headers, timeout):
        if body["model"] == "gpt-4o-mini":
            return _reply(prompt_tokens=300)                      # same decision, cheaper
        if body["model"] == "gpt-3.5-turbo":
            return _reply(args={"invoice": "WRONG"})              # wrong refund -> must be rejected
        assert [t["function"]["name"] for t in body["tools"]] == ["refund"]   # pruned request
        return _reply(prompt_tokens=120)
    monkeypatch.setattr(rp, "_post", fake_post)
    report = fx.find_fixes([_trace()], "openai:gpt-4o", ["openai:gpt-3.5-turbo", "openai:gpt-4o-mini"],
                           keys={"openai": "k"})
    by = {f.title: f for f in report.fixes}
    assert by["Switch openai:gpt-4o → openai:gpt-4o-mini"].verified
    assert not by["Switch openai:gpt-4o → openai:gpt-3.5-turbo"].verified
    prune = next(f for f in report.fixes if f.kind == "prune_tools")
    assert prune.verified and prune.input_tokens_change_pct == -60.0
    assert not report.fixes[-1].verified                          # rejected fixes rank last
    pol = report.policy()
    assert pol["model_map"] == {"gpt-4o": "gpt-4o-mini"}
    assert pol["drop_tools"] == ["cancel_subscription", "send_marketing_email"]
    assert "✅" in report.markdown() and "gpt-3.5-turbo" in report.markdown()


def test_policy_never_switches_provider(monkeypatch):
    monkeypatch.setattr(rp, "_post", lambda *a, **k: _reply())
    report = fx.find_fixes([_trace()], "openai:gpt-4o", ["groq:llama-3.3-70b-versatile"], keys={"groq": "k", "openai": "k"})
    assert report.fixes[0].verified
    assert "model_map" not in report.policy()     # user's OpenAI client can't call Groq


def test_apply_policy(tmp_path):
    (tmp_path / "runcore.fix.json").write_text(json.dumps(
        {"model_map": {"gpt-4o": "gpt-4o-mini"}, "drop_tools": ["cancel_subscription"]}))
    kwargs = {"model": "gpt-4o", "tools": [_tool("refund"), _tool("cancel_subscription")], "messages": []}
    new, applied = fx.apply_policy("openai", kwargs)
    assert new["model"] == "gpt-4o-mini" and [t["function"]["name"] for t in new["tools"]] == ["refund"]
    assert kwargs["model"] == "gpt-4o"           # caller's dict untouched
    assert applied == ["model:gpt-4o->gpt-4o-mini", "drop_tools:1"]
    only = {"model": "other", "tools": [_tool("cancel_subscription")], "tool_choice": "auto"}
    new, _ = fx.apply_policy("openai", only)
    assert "tools" not in new and "tool_choice" not in new and new["model"] == "other"
    anth, _ = fx.apply_policy("anthropic", {"model": "x", "tools": [{"name": "cancel_subscription"}, {"name": "a"}]})
    assert anth["tools"] == [{"name": "a"}]


def test_no_policy_is_passthrough():
    kwargs = {"model": "gpt-4o"}
    assert fx.apply_policy("openai", kwargs) == (kwargs, [])


def test_cli_fix(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from runcore.cli.main import app
    monkeypatch.setattr(rp, "_post", lambda *a, **k: _reply())
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    f = tmp_path / "t.json"
    f.write_text(json.dumps(_trace()))
    res = CliRunner().invoke(app, ["fix", str(f), "-c", "openai:gpt-4o", "-t", "openai:gpt-4o-mini",
                                   "--markdown", "pr.md"])
    assert res.exit_code == 0, res.output
    pol = json.loads((tmp_path / "runcore.fix.json").read_text())
    assert pol["model_map"] == {"gpt-4o": "gpt-4o-mini"}
    assert "verified fixes" in (tmp_path / "pr.md").read_text()
