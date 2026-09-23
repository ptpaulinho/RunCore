"""Alert notifiers — webhook, Slack, and console."""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from runcore.monitor.models import Alert, AlertSeverity


class ConsoleNotifier:
    """Prints alerts to stdout with colour coding."""

    _COLORS = {
        AlertSeverity.INFO:     "\033[36m",   # cyan
        AlertSeverity.WARNING:  "\033[33m",   # yellow
        AlertSeverity.CRITICAL: "\033[31m",   # red
    }
    _RESET = "\033[0m"
    _ICONS = {
        AlertSeverity.INFO: "ℹ",
        AlertSeverity.WARNING: "⚠",
        AlertSeverity.CRITICAL: "✖",
    }

    def send(self, alert: Alert) -> None:
        color = self._COLORS.get(alert.severity, "")
        icon = self._ICONS.get(alert.severity, "·")
        print(
            f"{color}{icon} [{alert.severity.value.upper()}] "
            f"{alert.agent_name} — {alert.message}{self._RESET}"
        )


class WebhookNotifier:
    """Posts alert JSON to a generic HTTP webhook (POST, application/json)."""

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout

    def send(self, alert: Alert) -> bool:
        payload = json.dumps(alert.to_dict()).encode()
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout):
                return True
        except Exception:
            return False


class SlackNotifier:
    """Posts a formatted message to a Slack Incoming Webhook URL."""

    _SEVERITY_EMOJI = {
        AlertSeverity.INFO:     ":information_source:",
        AlertSeverity.WARNING:  ":warning:",
        AlertSeverity.CRITICAL: ":red_circle:",
    }
    _SEVERITY_COLOR = {
        AlertSeverity.INFO:     "#36a64f",
        AlertSeverity.WARNING:  "#fbbf24",
        AlertSeverity.CRITICAL: "#ef4444",
    }

    def __init__(self, webhook_url: str, timeout: float = 5.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send(self, alert: Alert) -> bool:
        emoji = self._SEVERITY_EMOJI.get(alert.severity, ":bell:")
        color = self._SEVERITY_COLOR.get(alert.severity, "#64748b")
        payload = {
            "attachments": [{
                "color": color,
                "title": f"{emoji} RunCore Alert — {alert.alert_type.value}",
                "text": alert.message,
                "fields": [
                    {"title": "Agent",     "value": alert.agent_name,                    "short": True},
                    {"title": "Severity",  "value": alert.severity.value.upper(),        "short": True},
                    {"title": "Current",   "value": str(round(alert.current_value, 6)),  "short": True},
                    {"title": "Baseline",  "value": str(round(alert.baseline_value, 6)), "short": True},
                    {"title": "Delta",     "value": f"{alert.delta_pct:+.1f}%",          "short": True},
                    {"title": "Timestamp", "value": alert.timestamp.strftime("%H:%M:%S UTC"), "short": True},
                ],
                "footer": "RunCore Monitor",
            }]
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout):
                return True
        except Exception:
            return False


def build_notifiers(config) -> list:
    """Build notifier list from MonitorConfig."""
    notifiers = [ConsoleNotifier()]
    if config.webhook_url:
        notifiers.append(WebhookNotifier(config.webhook_url))
    if config.slack_webhook_url:
        notifiers.append(SlackNotifier(config.slack_webhook_url))
    return notifiers
