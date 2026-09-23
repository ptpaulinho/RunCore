"""Data models for the continuous monitoring daemon."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AlertSeverity(str, Enum):
    INFO    = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    CPST_DEGRADED       = "cpst_degraded"
    LOOP_RISK_HIGH      = "loop_risk_high"
    SUCCESS_RATE_DROP   = "success_rate_drop"
    COST_SPIKE          = "cost_spike"
    QUALITY_DROP        = "quality_drop"


@dataclass
class Alert:
    alert_type: AlertType
    severity: AlertSeverity
    agent_name: str
    message: str
    current_value: float
    baseline_value: float
    threshold: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def delta_pct(self) -> float:
        if self.baseline_value == 0:
            return 0.0
        return (self.current_value - self.baseline_value) / abs(self.baseline_value) * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "agent_name": self.agent_name,
            "message": self.message,
            "current_value": round(self.current_value, 6),
            "baseline_value": round(self.baseline_value, 6),
            "threshold": round(self.threshold, 4),
            "delta_pct": round(self.delta_pct, 2),
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class MonitorSnapshot:
    agent_name: str
    timestamp: datetime
    window_traces: int
    avg_cpst: float
    avg_loop_risk: float
    avg_cost: float
    success_rate: float
    avg_quality: float
    alerts: list[Alert] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "timestamp": self.timestamp.isoformat(),
            "window_traces": self.window_traces,
            "avg_cpst": round(self.avg_cpst, 6),
            "avg_loop_risk": round(self.avg_loop_risk, 4),
            "avg_cost": round(self.avg_cost, 6),
            "success_rate": round(self.success_rate, 4),
            "avg_quality": round(self.avg_quality, 4),
            "alerts": [a.to_dict() for a in self.alerts],
        }


@dataclass
class MonitorConfig:
    """Thresholds and settings for the monitoring daemon."""
    # CpST degrades by more than this fraction → WARNING
    cpst_warning_threshold_pct: float = 20.0
    # CpST degrades by more than this fraction → CRITICAL
    cpst_critical_threshold_pct: float = 50.0
    # Loop risk score above this → WARNING
    loop_risk_warning: float = 0.20
    # Loop risk score above this → CRITICAL
    loop_risk_critical: float = 0.40
    # Success rate drops by more than this fraction → WARNING
    success_rate_drop_pct: float = 10.0
    # Cost increases by more than this fraction → WARNING
    cost_spike_pct: float = 30.0
    # Quality drops by more than this fraction → WARNING
    quality_drop_pct: float = 15.0
    # Minimum traces in window before alerts are raised
    min_window_size: int = 5
    # How many recent traces constitute the "window"
    window_size: int = 20
    # Polling interval in seconds (for daemon mode)
    poll_interval_seconds: float = 60.0
    # Webhook URL for alerts (optional)
    webhook_url: str | None = None
    # Slack webhook URL (optional)
    slack_webhook_url: str | None = None
    # Alert only if this agent_name (None = all agents)
    agent_filter: str | None = None
