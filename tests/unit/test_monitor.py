"""Tests for the continuous monitoring module."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from runcore.monitor import (
    MonitorConfig, MonitorWatcher, MonitorSnapshot,
    Alert, AlertSeverity, AlertType,
)
from runcore.atir.spec import ATIRTrace, LLMSpan, ToolSpan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc)


def _make_trace(
    success: bool = True,
    cost: float = 0.001,
    input_tokens: int = 500,
    output_tokens: int = 100,
    n_tool: int = 3,
    n_dup: int = 0,
    quality: float | None = 0.9,
    agent_name: str = "test_agent",
) -> ATIRTrace:
    spans = [LLMSpan(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        started_at=_utcnow(),
        duration_ms=300.0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
    )]
    for i in range(n_tool):
        args = {"id": "X"} if i < n_dup else {"id": f"item-{i}"}
        spans.append(ToolSpan(
            name="get_item",
            started_at=_utcnow(),
            duration_ms=10.0,
            input_tokens=20,
            success=True,
            arguments=args,
        ))
    return ATIRTrace(
        trace_id=str(uuid.uuid4()),
        agent_name=agent_name,
        task="test",
        started_at=_utcnow(),
        success=success,
        quality_score=quality,
        provider="anthropic",
        framework="runcore",
        spans=spans,
    ).finalize()


def _make_traces(n: int = 10, **kwargs) -> list[ATIRTrace]:
    return [_make_trace(**kwargs) for _ in range(n)]


# ---------------------------------------------------------------------------
# MonitorWatcher — baseline
# ---------------------------------------------------------------------------

def test_set_baseline_returns_metrics():
    watcher = MonitorWatcher()
    traces = _make_traces(10)
    baseline = watcher.set_baseline(traces)
    assert "cpst" in baseline
    assert "avg_cost" in baseline
    assert "loop_risk" in baseline
    assert "success_rate" in baseline


def test_baseline_stored():
    watcher = MonitorWatcher()
    traces = _make_traces(10)
    watcher.set_baseline(traces)
    assert watcher.baseline is not None
    assert watcher.baseline["success_rate"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# MonitorWatcher — no alerts when healthy
# ---------------------------------------------------------------------------

def test_no_alerts_when_healthy():
    watcher = MonitorWatcher()
    baseline = _make_traces(10, cost=0.001)
    watcher.set_baseline(baseline)
    # Same metrics → no alerts
    current = _make_traces(10, cost=0.001)
    snapshot = watcher.check(current, agent_name="test_agent")
    assert snapshot.alerts == []


def test_snapshot_fields():
    watcher = MonitorWatcher()
    traces = _make_traces(10)
    watcher.set_baseline(traces)
    snapshot = watcher.check(traces, agent_name="my_agent")
    assert snapshot.agent_name == "my_agent"
    assert snapshot.window_traces == 10
    assert snapshot.success_rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# CpST degradation alerts
# ---------------------------------------------------------------------------

def test_cpst_warning_triggered():
    cfg = MonitorConfig(cpst_warning_threshold_pct=20.0, cpst_critical_threshold_pct=50.0)
    watcher = MonitorWatcher(config=cfg)
    baseline = _make_traces(10, cost=0.001, n_tool=5, n_dup=0)
    watcher.set_baseline(baseline)
    # 30% cost increase → CpST should degrade
    degraded = _make_traces(10, cost=0.0014, n_tool=5, n_dup=0)
    snapshot = watcher.check(degraded)
    cpst_alerts = [a for a in snapshot.alerts if a.alert_type == AlertType.CPST_DEGRADED]
    assert len(cpst_alerts) >= 1
    assert cpst_alerts[0].severity in (AlertSeverity.WARNING, AlertSeverity.CRITICAL)


def test_no_cpst_alert_when_improving():
    cfg = MonitorConfig(cpst_warning_threshold_pct=20.0)
    watcher = MonitorWatcher(config=cfg)
    baseline = _make_traces(10, cost=0.002)
    watcher.set_baseline(baseline)
    # Cost decreased → no CpST alert
    improved = _make_traces(10, cost=0.001)
    snapshot = watcher.check(improved)
    cpst_alerts = [a for a in snapshot.alerts if a.alert_type == AlertType.CPST_DEGRADED]
    assert cpst_alerts == []


# ---------------------------------------------------------------------------
# Loop risk alerts
# ---------------------------------------------------------------------------

def test_loop_risk_warning_triggered():
    cfg = MonitorConfig(loop_risk_warning=0.10)
    watcher = MonitorWatcher(config=cfg)
    # Baseline: no dups
    baseline = _make_traces(10, n_tool=4, n_dup=0)
    watcher.set_baseline(baseline)
    # Current: lots of dups → high loop risk
    high_dup = _make_traces(10, n_tool=4, n_dup=3)
    snapshot = watcher.check(high_dup)
    loop_alerts = [a for a in snapshot.alerts if a.alert_type == AlertType.LOOP_RISK_HIGH]
    assert len(loop_alerts) >= 1


def test_loop_risk_no_alert_when_low():
    cfg = MonitorConfig(loop_risk_warning=0.50)  # high threshold
    watcher = MonitorWatcher(config=cfg)
    baseline = _make_traces(10, n_tool=4, n_dup=0)
    watcher.set_baseline(baseline)
    current = _make_traces(10, n_tool=4, n_dup=0)
    snapshot = watcher.check(current)
    loop_alerts = [a for a in snapshot.alerts if a.alert_type == AlertType.LOOP_RISK_HIGH]
    assert loop_alerts == []


# ---------------------------------------------------------------------------
# Success rate drop
# ---------------------------------------------------------------------------

def test_success_rate_drop_alert():
    cfg = MonitorConfig(success_rate_drop_pct=15.0)
    watcher = MonitorWatcher(config=cfg)
    baseline = _make_traces(10, success=True)
    watcher.set_baseline(baseline)
    # Mix of success/failure — 50% success rate
    failing = [_make_trace(success=i < 5) for i in range(10)]
    snapshot = watcher.check(failing)
    sr_alerts = [a for a in snapshot.alerts if a.alert_type == AlertType.SUCCESS_RATE_DROP]
    assert len(sr_alerts) >= 1


# ---------------------------------------------------------------------------
# Alert model
# ---------------------------------------------------------------------------

def test_alert_to_dict():
    alert = Alert(
        alert_type=AlertType.CPST_DEGRADED,
        severity=AlertSeverity.WARNING,
        agent_name="my_agent",
        message="CpST degraded",
        current_value=0.002,
        baseline_value=0.001,
        threshold=20.0,
    )
    d = alert.to_dict()
    assert d["alert_type"] == "cpst_degraded"
    assert d["severity"] == "warning"
    assert d["delta_pct"] == pytest.approx(100.0)


def test_alert_delta_pct():
    alert = Alert(
        alert_type=AlertType.COST_SPIKE,
        severity=AlertSeverity.WARNING,
        agent_name="a",
        message="cost spike",
        current_value=0.003,
        baseline_value=0.002,
        threshold=30.0,
    )
    assert alert.delta_pct == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# MonitorSnapshot
# ---------------------------------------------------------------------------

def test_snapshot_to_dict():
    watcher = MonitorWatcher()
    traces = _make_traces(5)
    watcher.set_baseline(traces)
    snapshot = watcher.check(traces)
    d = snapshot.to_dict()
    assert "agent_name" in d
    assert "alerts" in d
    assert isinstance(d["alerts"], list)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def test_history_accumulates():
    watcher = MonitorWatcher()
    traces = _make_traces(10)
    watcher.set_baseline(traces)
    watcher.check(traces)
    watcher.check(traces)
    assert len(watcher.history) == 2


# ---------------------------------------------------------------------------
# MonitorConfig defaults
# ---------------------------------------------------------------------------

def test_monitor_config_defaults():
    cfg = MonitorConfig()
    assert cfg.cpst_warning_threshold_pct == 20.0
    assert cfg.loop_risk_warning == 0.20
    assert cfg.min_window_size == 5
    assert cfg.poll_interval_seconds == 60.0
