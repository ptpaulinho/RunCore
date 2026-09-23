"""Counterfactual replay: recording, format conversion, comparison, full re-run."""
import json

import pytest

import runcore
from runcore import replay as rp
from runcore.sdk.proxy import _recording


def _span(request, output, fmt="openai", model="gpt-4o", cost=0.001):
    return {"type": "llm_call", "provider": "openai", "model": model, "cost_usd": cost, "duration_ms": 900.0,
            "input_tokens": 100, "output_tokens": 20,
            "metadata": {"replay": {"format": fmt, "request": request, "output": output}}}


REQ = {"messages": [{"role": "system", "content": "You are support."},
                    {"role": "user", "content": "Refund INV-1"}],
       "tools": [{"type": "function", "function": {"name": "refund", "parameters": {"type": "object"}}}]}
REFUND = {"text": "", "tool_calls": [{"name": "refund", "arguments": {"invoice": "INV-1"}}]}


def _fake_openai(tool_args=None, text=""):
    msg = {"content": text}
    if tool_args is not None:
        msg["tool_calls"] = [{"id": "c1", "function": {"name": "refund", "arguments": json.dumps(tool_args)}}]
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


def test_compare_statuses():
    assert rp.compare(REFUND, REFUND)[0] == "same"
    other_args = {"text": "", "tool_calls": [{"name": "refund", "arguments": {"invoice": "INV-2"}}]}
    assert rp.compare(REFUND, other_args)[0] == "args_differ"
    assert rp.compare(REFUND, {"text": "Sorry, no.", "tool_calls": []})[0] == "diverged"
    assert rp.compare({"text": "Your refund is done."}, {"text": "Your refund is done!"})[0] == "same"
    assert rp.compare({"text": "Your refund is done."}, {"text": "Weather is sunny in Lisbon"})[0] == "changed"


def test_format_round_trip_keeps_tool_calls_and_results():
    anth = {"system": "sys", "tools": [{"name": "refund", "input_schema": {"type": "object"}}],
            "messages": [
                {"role": "user", "content": "Refund INV-1"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "refund",
                                                   "input": {"invoice": "INV-1"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]}
    oa = rp.anthropic_to_openai(anth)
    assert oa["messages"][0] == {"role": "system", "content": "sys"}
    assert oa["messages"][2]["tool_calls"][0]["function"]["name"] == "refund"
    assert oa["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "ok"}
    back = rp.openai_to_anthropic(oa)
    assert back["system"] == "sys" and back["tools"][0]["name"] == "refund"
    assert back["messages"][1]["content"][0]["input"] == {"invoice": "INV-1"}
    assert back["messages"][2]["content"][0]["type"] == "tool_result"


def test_recording_captures_request_and_decision():
    class Resp:
        def model_dump(self):
            return _fake_openai({"invoice": "INV-1"})
    rec = _recording("openai", {**REQ, "model": "gpt-4o", "stream": False}, Resp())
    assert rec["replay"]["request"]["messages"] == REQ["messages"]
    assert "model" not in rec["replay"]["request"]
    assert rec["replay"]["output"] == REFUND


def test_recording_opt_out(monkeypatch):
    monkeypatch.setenv("RUNCORE_RECORD_CONTENT", "0")
    assert _recording("openai", REQ, object()) == {}


def test_replay_report(monkeypatch):
    answers = iter([_fake_openai({"invoice": "INV-1"}), _fake_openai({"invoice": "WRONG"})])
    sent = []
    monkeypatch.setattr(rp, "_post", lambda url, body, headers, timeout: sent.append((url, body)) or next(answers))
    trace = {"trace_id": "t1", "spans": [_span(REQ, REFUND), _span(REQ, REFUND),
                                         {"type": "llm_call", "model": "x", "metadata": {}}]}
    report = rp.replay([trace], "openai:gpt-4o-mini", keys={"openai": "sk-test"})
    sm = report.summary()
    assert [s.status for s in report.steps] == ["same", "args_differ"]
    assert sm["agreement"] == 0.5 and sm["verdict"] == "unsafe" and sm["tool_decisions_changed"] == 1
    assert sm["skipped_without_recording"] == 1
    assert sm["candidate_cost_usd"] is not None and sm["cost_change_pct"] < 0   # gpt-4o-mini is cheaper
    assert sent[0][0] == "https://api.openai.com/v1/chat/completions"
    assert sent[0][1]["model"] == "gpt-4o-mini" and sent[0][1]["messages"] == REQ["messages"]


def test_replay_to_anthropic_and_unknown_price(monkeypatch):
    monkeypatch.setattr(rp, "_post", lambda url, body, headers, timeout: {
        "content": [{"type": "tool_use", "name": "refund", "input": {"invoice": "INV-1"}}],
        "usage": {"input_tokens": 90, "output_tokens": 9}})
    report = rp.replay([{"spans": [_span(REQ, REFUND)]}], "anthropic:some-new-model", keys={"anthropic": "k"})
    assert report.steps[0].status == "same"
    assert report.summary()["candidate_cost_usd"] is None   # never claim an unknown model is free


def test_replay_errors_are_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise rp.ReplayError("HTTP 401: bad key")
    monkeypatch.setattr(rp, "_post", boom)
    sm = rp.replay([{"spans": [_span(REQ, REFUND)]}], "groq:llama-3.3-70b-versatile", keys={"groq": "k"}).summary()
    assert sm["errors"] == 1 and sm["verdict"] == "no_data"


def test_judge_upgrades_text_steps(monkeypatch):
    replies = iter([{"choices": [{"message": {"content": "Refund issued for INV-1, 3-5 days."}}]},
                    {"choices": [{"message": {"content": "EQUIVALENT"}}]}])
    monkeypatch.setattr(rp, "_post", lambda *a, **k: next(replies))
    orig = {"text": "Done. Your refund for invoice INV-1 will arrive within five business days.", "tool_calls": []}
    report = rp.replay([{"spans": [_span(REQ, orig)]}], "openai:gpt-4o-mini", judge_target="openai:gpt-4o",
                       keys={"openai": "k"})
    assert report.steps[0].status == "equivalent"


def test_bad_target():
    with pytest.raises(rp.ReplayError):
        rp.parse_target("gpt-4o")


def test_recorded_tools_full_rerun():
    calls = []

    @runcore.tool
    def refund(invoice: str) -> dict:
        calls.append(invoice)
        return {"refunded": invoice}

    with runcore.capture("agent") as cap:
        refund("INV-1")
    trace = cap.get_atir().model_dump(mode="json")
    assert trace["spans"][0]["metadata"]["result"] == {"refunded": "INV-1"}

    with rp.recorded_tools(trace), runcore.capture("agent-v2") as cap2:
        assert refund("INV-1") == {"refunded": "INV-1"}      # served from the recording
        with pytest.raises(rp.ReplayMiss):
            refund("INV-999")                                 # never recorded -> no real side effect
    assert calls == ["INV-1"]
    assert cap2.get_atir().spans[0].metadata["replayed"] is True
    with rp.recorded_tools(trace):
        assert refund("INV-1") == {"refunded": "INV-1"}      # works outside capture() too
    assert calls == ["INV-1"]


def test_cli(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from runcore.cli.main import app
    monkeypatch.setattr(rp, "_post", lambda *a, **k: _fake_openai({"invoice": "INV-1"}))
    monkeypatch.setenv("GROQ_API_KEY", "k")
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"trace_id": "abc", "spans": [_span(REQ, REFUND)]}))
    out = tmp_path / "r.json"
    res = CliRunner().invoke(app, ["replay", str(f), "-m", "groq:llama-3.3-70b-versatile", "-o", str(out),
                                   "--fail-below", "0.9"])
    assert res.exit_code == 0, res.output
    assert "SAFE" in res.output
    assert json.loads(out.read_text())["summary"]["agreement"] == 1.0



def test_sdk_junk_fields_stripped_before_resend(monkeypatch):
    sent = []
    monkeypatch.setattr(rp, "_post", lambda url, body, h, t: sent.append(body) or _fake_openai(text="ok"))
    req = {"messages": [{"role": "user", "content": "hi"},
                        {"content": "", "refusal": None, "role": "assistant", "annotations": None, "audio": None,
                         "function_call": None, "tool_calls": [{"id": "c1", "index": 0, "type": "function",
                                                                "function": {"name": "f", "arguments": "{}"}}]},
                        {"role": "tool", "tool_call_id": "c1", "content": "r"}]}
    rp.call_model("groq:x", req, api_key="k")
    assistant = sent[0]["messages"][1]
    assert set(assistant) == {"role", "content", "tool_calls"} and assistant["content"] is None
    assert set(assistant["tool_calls"][0]) == {"id", "type", "function"}
