"""MonitorDaemon — polls a trace source and runs MonitorWatcher on each window."""
from __future__ import annotations

import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from runcore.atir.spec import ATIRTrace
from runcore.atir.converter import from_dict
from runcore.monitor.models import MonitorConfig, MonitorSnapshot
from runcore.monitor.notifier import build_notifiers
from runcore.monitor.watcher import MonitorWatcher


TraceSource = Callable[[], list[ATIRTrace]]


class MonitorDaemon:
    """Polls *trace_source* every *config.poll_interval_seconds* seconds,
    runs MonitorWatcher, and dispatches alerts through registered notifiers.

    Usage::

        from runcore.monitor import MonitorDaemon, MonitorConfig

        def my_trace_source():
            # Return the last N ATIRTraces from your storage backend
            return load_recent_traces(n=20)

        daemon = MonitorDaemon(
            trace_source=my_trace_source,
            config=MonitorConfig(
                cpst_warning_threshold_pct=25.0,
                slack_webhook_url="https://hooks.slack.com/...",
            )
        )
        daemon.run()   # blocks; Ctrl+C to stop
    """

    def __init__(
        self,
        trace_source: TraceSource,
        config: MonitorConfig | None = None,
        baseline_traces: list[ATIRTrace] | None = None,
    ) -> None:
        self.trace_source = trace_source
        self.config = config or MonitorConfig()
        self.watcher = MonitorWatcher(config=self.config)
        self.notifiers = build_notifiers(self.config)
        self._running = False
        self._snapshots: list[MonitorSnapshot] = []

        if baseline_traces:
            self.watcher.set_baseline(baseline_traces)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the polling loop. Blocks until Ctrl+C or stop() is called."""
        self._running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        print(f"[RunCore Monitor] Started. Polling every {self.config.poll_interval_seconds}s")
        if self.watcher.baseline:
            print(f"[RunCore Monitor] Baseline set: CpST=${self.watcher.baseline.get('cpst', 0):.5f}")

        while self._running:
            try:
                self._tick()
            except Exception as exc:
                print(f"[RunCore Monitor] Error during tick: {exc}")
            if self._running:
                time.sleep(self.config.poll_interval_seconds)

        print("[RunCore Monitor] Stopped.")

    def tick_once(self) -> MonitorSnapshot | None:
        """Run a single monitoring cycle and return the snapshot (non-blocking)."""
        return self._tick()

    def stop(self) -> None:
        self._running = False

    @property
    def snapshots(self) -> list[MonitorSnapshot]:
        return list(self._snapshots)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tick(self) -> MonitorSnapshot | None:
        traces = self.trace_source()
        if not traces:
            return None

        # Auto-set baseline from first window if not already set
        if self.watcher.baseline is None and len(traces) >= self.config.min_window_size:
            self.watcher.set_baseline(traces)
            print(f"[RunCore Monitor] Baseline auto-set from {len(traces)} traces")
            return None

        snapshot = self.watcher.check(traces)
        self._snapshots.append(snapshot)

        # Dispatch alerts
        for alert in snapshot.alerts:
            for notifier in self.notifiers:
                try:
                    notifier.send(alert)
                except Exception as exc:
                    print(f"[RunCore Monitor] Notifier error: {exc}")

        if not snapshot.alerts:
            ts = snapshot.timestamp.strftime("%H:%M:%S")
            print(
                f"[RunCore Monitor] {ts} OK — "
                f"CpST=${snapshot.avg_cpst:.5f} "
                f"loop_risk={snapshot.avg_loop_risk:.3f} "
                f"success={snapshot.success_rate*100:.0f}%"
            )

        return snapshot

    def _handle_signal(self, signum, frame) -> None:
        print("\n[RunCore Monitor] Signal received — stopping…")
        self._running = False


# ---------------------------------------------------------------------------
# File-based trace source helper
# ---------------------------------------------------------------------------

class FileTraceSource:
    """Loads ATIR traces from a directory of *.atir.json files.

    Useful for watching a local directory written by ``runcore capture``
    or ``runcore instrument``.
    """

    def __init__(self, directory: str | Path, window: int = 20) -> None:
        self.directory = Path(directory)
        self.window = window

    def __call__(self) -> list[ATIRTrace]:
        files = sorted(
            self.directory.glob("*.atir.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:self.window]
        traces = []
        for f in files:
            try:
                data = json.loads(f.read_text())
                traces.append(from_dict(data))
            except Exception:
                pass
        return traces


# ---------------------------------------------------------------------------
# RunCore report-based trace source
# ---------------------------------------------------------------------------

class RunCoreReportSource:
    """Loads traces from RunCore's .runcore/reports/*.json benchmark reports."""

    def __init__(self, reports_dir: str | Path = ".runcore/reports", window: int = 20) -> None:
        self.reports_dir = Path(reports_dir)
        self.window = window

    def __call__(self) -> list[ATIRTrace]:
        from runcore.atir.converter import agent_trace_to_atir
        from runcore.core.models import AgentTrace

        files = sorted(
            self.reports_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:self.window]

        traces = []
        for f in files:
            try:
                data = json.loads(f.read_text())
                # Each report contains baseline traces in a nested structure
                for key in ("baseline_traces", "optimized_traces"):
                    for raw in data.get(key, []):
                        try:
                            at = AgentTrace.model_validate(raw)
                            traces.append(agent_trace_to_atir(at))
                        except Exception:
                            pass
            except Exception:
                pass
        return traces
