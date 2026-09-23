"""Unit tests for the CI-gate comparison logic (no LLM calls)."""
import pytest
from runcore.cli.main import evaluate_ci


def _m(cost=0.0, tokens=1000.0, success=1.0):
    return {"avg_cost_per_run": cost, "avg_tokens_per_run": tokens, "success_rate": success}


def test_pass_when_unchanged():
    base = _m(cost=0.01, tokens=1000, success=1.0)
    cost_inc, drop, fails = evaluate_ci(base, dict(base), 10, 5)
    assert fails == []
    assert cost_inc == 0 and drop == 0


def test_fail_on_cost_increase():
    base = _m(cost=0.010, success=1.0)
    cur = _m(cost=0.012, success=1.0)          # +20%
    cost_inc, _, fails = evaluate_ci(base, cur, max_cost_increase=10, max_success_drop=5)
    assert cost_inc == 20
    assert any("cost/run" in f for f in fails)


def test_fail_on_success_drop():
    base = _m(cost=0.01, success=1.0)
    cur = _m(cost=0.01, success=0.90)          # -10 points
    _, drop, fails = evaluate_ci(base, cur, max_cost_increase=10, max_success_drop=5)
    assert drop == pytest.approx(10.0)
    assert any("success" in f for f in fails)


def test_free_provider_falls_back_to_tokens():
    base = _m(cost=0.0, tokens=1000, success=1.0)
    cur = _m(cost=0.0, tokens=1300, success=1.0)   # +30% tokens
    cost_inc, _, fails = evaluate_ci(base, cur, max_cost_increase=10, max_success_drop=5)
    assert cost_inc == pytest.approx(30.0)
    assert any("tokens/run" in f for f in fails)


def test_min_success_floor():
    base = _m(cost=0.01, success=0.5)
    cur = _m(cost=0.01, success=0.5)
    _, _, fails = evaluate_ci(base, cur, 10, 100, min_success=0.8)
    assert any("floor" in f for f in fails)


def test_improvement_passes():
    base = _m(cost=0.010, tokens=1000, success=0.9)
    cur = _m(cost=0.008, tokens=800, success=1.0)   # cheaper AND better
    cost_inc, drop, fails = evaluate_ci(base, cur, 10, 5)
    assert fails == []
    assert cost_inc < 0 and drop < 0
