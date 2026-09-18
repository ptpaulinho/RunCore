"""Unit tests for the @runcore.tool decorator (dedup-aware tool wrapping)."""
import pytest

import runcore
from runcore import GuardConfig


def test_passthrough_without_active_capture():
    calls = []

    @runcore.tool
    def add(a, b):
        calls.append((a, b))
        return a + b

    assert add(1, 2) == 3
    assert calls == [(1, 2)]


def test_duplicate_call_skips_execution_and_returns_cached_result():
    calls = []

    @runcore.tool
    def lookup(city):
        calls.append(city)
        return {"city": city, "temp": 21}

    with runcore.capture("agent", guards=GuardConfig(dedup_scope="session")) as cap:
        r1 = lookup("Lisbon")
        r2 = lookup("Lisbon")  # duplicate -> should NOT call the underlying fn again

    assert r1 == r2 == {"city": "Lisbon", "temp": 21}
    assert calls == ["Lisbon"]  # only executed once


def test_different_arguments_both_execute():
    calls = []

    @runcore.tool
    def lookup(city):
        calls.append(city)
        return {"city": city}

    with runcore.capture("agent", guards=GuardConfig(dedup_scope="session")) as cap:
        lookup("Lisbon")
        lookup("Porto")

    assert calls == ["Lisbon", "Porto"]


def test_cache_scoped_per_capture_not_leaked_across_runs():
    calls = []

    @runcore.tool
    def lookup(city):
        calls.append(city)
        return {"city": city}

    with runcore.capture("agent1", guards=GuardConfig(dedup_scope="session")):
        lookup("Lisbon")

    with runcore.capture("agent2", guards=GuardConfig(dedup_scope="session")):
        lookup("Lisbon")  # different capture/session -> executes again, no cross-leak

    assert calls == ["Lisbon", "Lisbon"]


def test_exception_propagates_and_is_recorded():
    @runcore.tool
    def boom():
        raise ValueError("nope")

    with runcore.capture("agent", guards=GuardConfig()) as cap:
        with pytest.raises(ValueError):
            boom()

    trace = cap.get_atir()
    tool_spans = [s for s in trace.spans if s.type == "tool_call"]
    assert len(tool_spans) == 1
    assert tool_spans[0].success is False


def test_custom_name():
    @runcore.tool(name="custom_search")
    def search(q):
        return {"q": q}

    with runcore.capture("agent", guards=GuardConfig()) as cap:
        search("hello")

    trace = cap.get_atir()
    tool_spans = [s for s in trace.spans if s.type == "tool_call"]
    assert tool_spans[0].name == "custom_search"


def test_works_without_guards_configured():
    """No GuardConfig -> no dedup, but the tool still runs and is recorded plainly."""
    calls = []

    @runcore.tool
    def echo(x):
        calls.append(x)
        return x

    with runcore.capture("agent") as cap:  # no guards=...
        echo(1)
        echo(1)

    assert calls == [1, 1]  # no guard engine -> no dedup, both execute
