"""MonitorWatcher — the core engine of the continuous monitoring daemon.

Analyzes a sliding window of ATIR traces, compares against a baseline,
and generates alerts when key metrics degrade.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from typing import Any

from runcore.atir.spec import ATIRTrace
from runcore.monitor.models import (
    Alert, AlertSeverity, AlertType,
    MonitorConfig, MonitorSnapshot,
)


class MonitorWatcher:
    """Analyzes a window of ATIR traces and emits alerts on metric degradation.

    Usage::

        watcher = MonitorWatcher(config=MonitorConfig())

        # Establish baseline from first N traces
        watcher.set_baseline(initial_traces)

        # On each new batch of traces:
        snapshot = watcher.check(new_traces)
        for alert in snapshot.alerts:
            print(alert.severity, alert.message)
    """

    def __init__(self, config: MonitorConfig | None = None) -> None:
        self.config = config or MonitorConfig()
        self._baseline: dict[str, float] | None = None
        self._history: list[MonitorSnapshot] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_baseline(self, traces: list[ATIRTrace]) -> dict[str, float]:
        """Compute baseline metrics from *traces* and store them.

        Call this once with a representative set of traces before monitoring.
        """
        self._baseline = self._compute_metrics(traces)
        return dict(self._baseline)

    def check(
        self,
        traces: list[ATIRTrace],
        agent_name: str | None = None,
    ) -> MonitorSnapshot:
        """Analyze *traces* and return a snapshot with any triggered alerts."""
        name = agent_name or (traces[0].agent_name if traces else "unknown")
        metrics = self._compute_metrics(traces)
        alerts = self._evaluate_alerts(name, metrics)

        snapshot = MonitorSnapshot(
            agent_name=name,
            timestamp=datetime.now(timezone.utc),
            window_traces=len(traces),
            avg_cpst=metrics["cpst"],
            avg_loop_risk=metrics["loop_risk"],
            avg_cost=metrics["avg_cost"],
            success_rate=metrics["success_rate"],
            avg_quality=metrics["avg_quality"],
            alerts=alerts,
        )
        self._history.append(snapshot)
        return snapshot

    @property
    def history(self) -> list[MonitorSnapshot]:
        return list(self._history)

    @property
    def baseline(self) -> dict[str, float] | None:
        return dict(self._baseline) if self._baseline else None

    def status_summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of the current monitor state."""
        if not self._history:
            return {"status": "no_data", "snapshots": 0}
        latest = self._history[-1]
        all_alerts = [a for snap in self._history for a in snap.alerts]
        criticals = [a for a in all_alerts if a.severity == AlertSeverity.CRITICAL]
        warnings = [a for a in all_alerts if a.severity == AlertSeverity.WARNING]
        return {
            "status": "critical" if criticals else "warning" if warnings else "ok",
            "snapshots": len(self._history),
            "latest": latest.to_dict(),
            "total_alerts": len(all_alerts),
            "critical_alerts": len(criticals),
            "warning_alerts": len(warnings),
            "baseline": self._baseline,
        }

    # ------------------------------------------------------------------
    # Internal: metrics
    # ------------------------------------------------------------------

    def _compute_metrics(self, traces: list[ATIRTrace]) -> dict[str, float]:
        if not traces:
            return {"cpst": 0, "loop_risk": 0, "avg_cost": 0, "success_rate": 0, "avg_quality": 0}

        cpsts, costs, loop_risks, qualities = [], [], [], []
        successes = []

        for t in traces:
            agg = t.aggregates
            if agg:
                cpsts.append(agg.cost_per_successful_task)
                costs.append(agg.total_cost_usd)
                # Loop risk from dup ratio
                if agg.tool_calls > 0:
                    loop_risks.append(
                        min(agg.duplicate_tool_calls / agg.tool_calls * 1.5, 1.0)
                    )
            successes.append(1.0 if t.success else 0.0)
            if t.quality_score is not None:
                qualities.append(t.quality_score)

        return {
            "cpst": statistics.mean(cpsts) if cpsts else 0.0,
            "avg_cost": statistics.mean(costs) if costs else 0.0,
            "loop_risk": statistics.mean(loop_risks) if loop_risks else 0.0,
            "success_rate": statistics.mean(successes) if successes else 0.0,
            "avg_quality": statistics.mean(qualities) if qualities else 0.0,
        }

    # ------------------------------------------------------------------
    # Internal: alert evaluation
    # ------------------------------------------------------------------

    def _evaluate_alerts(
        self, agent_name: str, current: dict[str, float]
    ) -> list[Alert]:
        alerts: list[Alert] = []
        cfg = self.config

        if len(self._history) < (cfg.min_window_size - 1) and not self._baseline:
            return []  # not enough data yet

        baseline = self._baseline or current  # use current as baseline if not set

        # --- CpST degradation ---
        b_cpst = baseline.get("cpst", 0)
        c_cpst = current["cpst"]
        if b_cpst > 0 and c_cpst > b_cpst:
            delta_pct = (c_cpst - b_cpst) / b_cpst * 100
            if delta_pct >= cfg.cpst_critical_threshold_pct:
                alerts.append(Alert(
                    alert_type=AlertType.CPST_DEGRADED,
                    severity=AlertSeverity.CRITICAL,
                    agent_name=agent_name,
                    message=f"CpST degraded by {delta_pct:.1f}% (${c_cpst:.5f} vs baseline ${b_cpst:.5f})",
                    current_value=c_cpst,
                    baseline_value=b_cpst,
                    threshold=cfg.cpst_critical_threshold_pct,
                ))
            elif delta_pct >= cfg.cpst_warning_threshold_pct:
                alerts.append(Alert(
                    alert_type=AlertType.CPST_DEGRADED,
                    severity=AlertSeverity.WARNING,
                    agent_name=agent_name,
                    message=f"CpST degraded by {delta_pct:.1f}% (${c_cpst:.5f} vs baseline ${b_cpst:.5f})",
                    current_value=c_cpst,
                    baseline_value=b_cpst,
                    threshold=cfg.cpst_warning_threshold_pct,
                ))

        # --- Loop risk ---
        lr = current["loop_risk"]
        if lr >= cfg.loop_risk_critical:
            alerts.append(Alert(
                alert_type=AlertType.LOOP_RISK_HIGH,
                severity=AlertSeverity.CRITICAL,
                agent_name=agent_name,
                message=f"Loop risk score {lr:.3f} exceeds critical threshold {cfg.loop_risk_critical}",
                current_value=lr,
                baseline_value=baseline.get("loop_risk", 0),
                threshold=cfg.loop_risk_critical,
            ))
        elif lr >= cfg.loop_risk_warning:
            alerts.append(Alert(
                alert_type=AlertType.LOOP_RISK_HIGH,
                severity=AlertSeverity.WARNING,
                agent_name=agent_name,
                message=f"Loop risk score {lr:.3f} exceeds warning threshold {cfg.loop_risk_warning}",
                current_value=lr,
                baseline_value=baseline.get("loop_risk", 0),
                threshold=cfg.loop_risk_warning,
            ))

        # --- Success rate drop ---
        b_sr = baseline.get("success_rate", 1.0)
        c_sr = current["success_rate"]
        if b_sr > 0 and c_sr < b_sr:
            drop_pct = (b_sr - c_sr) / b_sr * 100
            if drop_pct >= cfg.success_rate_drop_pct:
                sev = AlertSeverity.CRITICAL if drop_pct >= cfg.success_rate_drop_pct * 2 else AlertSeverity.WARNING
                alerts.append(Alert(
                    alert_type=AlertType.SUCCESS_RATE_DROP,
                    severity=sev,
                    agent_name=agent_name,
                    message=f"Success rate dropped {drop_pct:.1f}% ({c_sr*100:.0f}% vs baseline {b_sr*100:.0f}%)",
                    current_value=c_sr,
                    baseline_value=b_sr,
                    threshold=cfg.success_rate_drop_pct,
                ))

        # --- Cost spike ---
        b_cost = baseline.get("avg_cost", 0)
        c_cost = current["avg_cost"]
        if b_cost > 0 and c_cost > b_cost:
            spike_pct = (c_cost - b_cost) / b_cost * 100
            if spike_pct >= cfg.cost_spike_pct:
                alerts.append(Alert(
                    alert_type=AlertType.COST_SPIKE,
                    severity=AlertSeverity.WARNING,
                    agent_name=agent_name,
                    message=f"Avg cost spiked {spike_pct:.1f}% (${c_cost:.5f} vs baseline ${b_cost:.5f})",
                    current_value=c_cost,
                    baseline_value=b_cost,
                    threshold=cfg.cost_spike_pct,
                ))

        # --- Quality drop ---
        b_qual = baseline.get("avg_quality", 0)
        c_qual = current["avg_quality"]
        if b_qual > 0 and c_qual < b_qual:
            drop_pct = (b_qual - c_qual) / b_qual * 100
            if drop_pct >= cfg.quality_drop_pct:
                alerts.append(Alert(
                    alert_type=AlertType.QUALITY_DROP,
                    severity=AlertSeverity.WARNING,
                    agent_name=agent_name,
                    message=f"Quality dropped {drop_pct:.1f}% ({c_qual:.3f} vs baseline {b_qual:.3f})",
                    current_value=c_qual,
                    baseline_value=b_qual,
                    threshold=cfg.quality_drop_pct,
                ))

        return alerts
