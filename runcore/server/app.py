"""RunCore Web Dashboard — FastAPI server."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncio
import queue

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from pydantic import BaseModel

from runcore.agents.simulated import SimulatedAgentFactory
from runcore.benchmark.runner import BenchmarkRunner
from runcore.benchmark.metrics import calculate_metrics
from runcore.benchmark.comparison import BenchmarkComparison
from runcore.core.models import OptimizationConfig
from runcore.reports.generator import ReportGenerator

app = FastAPI(title="RunCore Dashboard", version="0.4.0")

# ---------------------------------------------------------------------------
# Cloud storage — initialise on startup
# ---------------------------------------------------------------------------
from runcore.server import storage as _store
from runcore.server import billing as _billing
from runcore.server import stripe_billing as _stripe
from runcore.server.config import load_into_env, set_keys, provider_status as _provider_status_fn
import runcore.server.config as _config

@app.on_event("startup")
def _startup():
    _store.init_db()
    _config.load_into_env()

_REPORTS_DIR = Path(".runcore/reports")
_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory run registry
_runs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

# SSE event queues per run_id
_sse_queues: dict[str, list[queue.Queue]] = {}
_sse_lock = threading.Lock()


def _sse_emit(run_id: str, event: str, data: str) -> None:
    """Push an SSE event to all listeners for *run_id*."""
    msg = f"event: {event}\ndata: {data}\n\n"
    with _sse_lock:
        for q in _sse_queues.get(run_id, []):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class BenchmarkRequest(BaseModel):
    agent: str = "support"
    tasks: list[str] = ["Refund invoice #1001 for customer@example.com"]
    runs_per_task: int = 5
    config: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------

def _run_benchmark(run_id: str, req: BenchmarkRequest) -> None:
    with _lock:
        _runs[run_id]["status"] = "running"
    _sse_emit(run_id, "status", json.dumps({"status": "running", "phase": "baseline", "pct": 0}))

    try:
        factory = SimulatedAgentFactory()
        agent = factory.create(req.agent)
        runner = BenchmarkRunner()

        valid_fields = OptimizationConfig.model_fields
        config = OptimizationConfig(**{k: v for k, v in req.config.items() if k in valid_fields})

        _sse_emit(run_id, "status", json.dumps({"status": "running", "phase": "baseline", "pct": 10}))
        baseline = runner.run_baseline(agent, req.tasks, runs_per_task=req.runs_per_task)
        _sse_emit(run_id, "status", json.dumps({"status": "running", "phase": "optimized", "pct": 50}))
        optimized = runner.run_optimized(agent, req.tasks, config, runs_per_task=req.runs_per_task, baseline_traces=baseline)
        _sse_emit(run_id, "status", json.dumps({"status": "running", "phase": "analysis", "pct": 85}))

        bm = calculate_metrics(baseline)
        om = calculate_metrics(optimized)

        cmp = BenchmarkComparison()
        result = cmp.compare(bm, om, config, baseline, optimized)

        # Run OptimizationAdvisor on baseline ATIR traces
        from runcore.advisor import OptimizationAdvisor
        from runcore.atir.converter import agent_trace_to_atir
        atir_traces = [agent_trace_to_atir(t) for t in baseline]
        advisor = OptimizationAdvisor()
        advice_report = advisor.analyze(atir_traces, agent_name=req.agent)

        gen = ReportGenerator()
        report_base = str(_REPORTS_DIR / run_id)
        json_path = gen.save_report(result, report_base + ".json", "json", bm, om)
        html_path = gen.save_report(result, report_base + ".html", "html", bm, om)

        # Save advisor report alongside
        advice_path = report_base + ".advice.json"
        Path(advice_path).write_text(json.dumps(advice_report.to_dict(), indent=2))

        with _lock:
            _runs[run_id].update({
                "status": "done",
                "result": result.result,
                "cost_savings_pct": result.cost_savings_pct,
                "token_reduction_pct": result.token_reduction_pct,
                "tool_call_reduction_pct": result.tool_call_reduction_pct,
                "latency_change_pct": result.latency_change_pct,
                "baseline_cost": bm.avg_cost,
                "optimized_cost": om.avg_cost,
                "baseline_success": bm.success_rate,
                "optimized_success": om.success_rate,
                "runs": result.runs,
                "json_path": json_path,
                "html_path": html_path,
                "advice_path": advice_path,
                "advice": advice_report.to_dict(),
                "agent": req.agent,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })

        _sse_emit(run_id, "done", json.dumps({
            "status": "done",
            "pct": 100,
            "result": result.result,
            "cost_savings_pct": result.cost_savings_pct,
            "total_estimated_savings_pct": round(advice_report.total_estimated_savings_pct(), 1),
            "prescriptions": len(advice_report.prescriptions),
        }))
    except Exception as exc:
        with _lock:
            _runs[run_id]["status"] = "error"
            _runs[run_id]["error"] = str(exc)
        _sse_emit(run_id, "error", json.dumps({"status": "error", "error": str(exc)}))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_P_BADGE = {
    "dedup_tool_calls":      ("Dedup",       "p-badge-dedup"),
    "context_compression":   ("Context",     "p-badge-context"),
    "schema_slim":           ("Schema",      "p-badge-schema"),
    "replacement_candidate": ("Replace",     "p-badge-replace"),
    "loop_break":            ("Loop Risk",   "p-badge-loop"),
    "cache_warm":            ("Cache",       "p-badge-cache"),
}

_DESIGN_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
  --bg: #070c19;
  --surface: #0d1830;
  --surface2: #121f3d;
  --surface3: #1a2d52;
  --border: rgba(91,138,247,0.13);
  --border-m: rgba(91,138,247,0.26);
  --text: #eef2ff;
  --text2: #94a3b8;
  --muted: #4e6080;
  --accent: #6488f5;
  --accent-2: #8aaaf8;
  --green: #34d399;
  --red: #f87171;
  --yellow: #fbbf24;
  --blue: #60a5fa;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  font-size: 14px;
  line-height: 1.6;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--surface3); border-radius: 3px; }

/* ── Nav ── */
.nav {
  background: rgba(6,11,24,0.85);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border);
  padding: 0 32px;
  display: flex;
  align-items: center;
  height: 60px;
  gap: 0;
  position: sticky;
  top: 0;
  z-index: 100;
}
.nav-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 1.2rem;
  font-weight: 800;
  letter-spacing: -0.5px;
  background: linear-gradient(135deg, #6488f5, #8aaaf8);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-right: 32px;
}
.nav-logo svg { flex-shrink: 0; }
.nav-links { display: flex; align-items: center; gap: 4px; }
.nav-link {
  padding: 6px 14px;
  border-radius: 8px;
  font-size: .84rem;
  font-weight: 500;
  color: var(--text2);
  text-decoration: none;
  transition: all .15s;
}
.nav-link:hover { color: var(--text); background: var(--surface2); }
.nav-link.active { color: var(--accent); background: rgba(91,138,247,0.1); }
.nav-right { display: flex; align-items: center; gap: 10px; font-size: .82rem; color: var(--muted); margin-left: auto; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); flex-shrink: 0; }
.status-dot.idle { background: var(--muted); }
@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.3; } }
.status-dot:not(.idle) { animation: pulse 2s infinite; }
.refresh-indicator { display: flex; align-items: center; gap: 5px; color: var(--muted); font-size: .78rem; }

/* ── Page ── */
.page { padding: 28px 32px; max-width: 1440px; margin: 0 auto; }

/* ── KPI Grid ── */
.kpi-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 24px; }
.kpi-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 20px 22px;
  display: flex;
  align-items: center;
  gap: 16px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  transition: transform .2s, box-shadow .2s;
}
.kpi-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, #5577f3, #4a6cf5);
}
.kpi-card:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(0,0,0,0.4); }
.kpi-icon {
  width: 44px; height: 44px;
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.kpi-label { font-size: .72rem; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 4px; font-weight: 600; }
.kpi-value { font-size: 1.9rem; font-weight: 800; line-height: 1; letter-spacing: -1px; }
.kpi-sub { font-size: .75rem; color: var(--muted); margin-top: 5px; }

/* ── Main grid ── */
.main-grid { display: grid; grid-template-columns: 320px 1fr; gap: 20px; margin-bottom: 24px; }

/* ── Cards ── */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 24px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.3);
}
.card-title {
  font-size: .85rem;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  gap: 8px;
  letter-spacing: .2px;
}
.card-title svg { color: var(--accent); opacity: .8; flex-shrink: 0; }

/* ── Form ── */
.field { margin-bottom: 14px; }
.field label {
  display: block;
  font-size: .72rem;
  text-transform: uppercase;
  letter-spacing: .8px;
  color: var(--muted);
  margin-bottom: 6px;
  font-weight: 600;
}
.field input, .field select, .field textarea {
  width: 100%;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  padding: 9px 12px;
  font-size: .88rem;
  font-family: inherit;
  outline: none;
  transition: border-color .15s, background .15s;
}
.field input:focus, .field select:focus, .field textarea:focus {
  border-color: var(--accent);
  background: var(--surface3);
}
.field textarea { height: 88px; resize: vertical; font-family: 'JetBrains Mono', 'SF Mono', monospace; font-size: .82rem; line-height: 1.5; }
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.btn-run {
  width: 100%;
  padding: 11px;
  background: linear-gradient(135deg, #5577f3, #4a6cf5);
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: .92rem;
  font-weight: 700;
  cursor: pointer;
  margin-top: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-family: inherit;
  box-shadow: 0 0 20px rgba(85,119,243,0.25);
  transition: all .2s;
}
.btn-run:hover { box-shadow: 0 0 28px rgba(85,119,243,0.4); transform: translateY(-1px); }
.btn-run:disabled { background: var(--surface2); color: var(--muted); cursor: not-allowed; box-shadow: none; transform: none; }
.run-msg { font-size: .82rem; color: var(--green); margin-top: 10px; min-height: 18px; text-align: center; }
.run-msg.err { color: var(--red); }

/* ── Progress bar ── */
@keyframes shimmer { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
.progress-wrap { margin-top: 12px; display: none; }
.progress-header { display: flex; justify-content: space-between; font-size: .76rem; color: var(--muted); margin-bottom: 6px; }
.progress-track { height: 4px; background: var(--surface2); border-radius: 2px; overflow: hidden; }
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #5577f3 0%, #6488f5 50%, #5577f3 100%);
  background-size: 200% 100%;
  border-radius: 2px;
  width: 0%;
  transition: width .4s ease;
  animation: shimmer 2s linear infinite;
}
.progress-fill.done { background: var(--green); animation: none; }

/* ── Charts ── */
.charts-col { display: flex; flex-direction: column; gap: 16px; }
.chart-wrap { position: relative; height: 200px; }
.chart-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: var(--muted);
  font-size: .85rem;
  flex-direction: column;
  gap: 10px;
}
.chart-empty svg { opacity: .25; }

/* ── Filters ── */
.filters {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
.filter-group { display: flex; align-items: center; gap: 6px; }
.filter-group label { font-size: .75rem; color: var(--muted); white-space: nowrap; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; }
.filter-group select, .filter-group input {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 7px;
  color: var(--text);
  padding: 5px 10px;
  font-size: .8rem;
  outline: none;
  font-family: inherit;
  transition: border-color .15s;
}
.filter-group select:focus, .filter-group input:focus { border-color: var(--accent); }
.btn-reset {
  padding: 5px 12px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 7px;
  color: var(--muted);
  font-size: .8rem;
  cursor: pointer;
  font-family: inherit;
  transition: all .15s;
}
.btn-reset:hover { color: var(--text); border-color: var(--border-m); }
.f-count { font-size: .78rem; color: var(--muted); margin-left: auto; }

/* ── Table ── */
table { width: 100%; border-collapse: collapse; font-size: .84rem; }
thead th {
  text-align: left;
  padding: 9px 14px;
  font-size: .7rem;
  text-transform: uppercase;
  letter-spacing: .8px;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  font-weight: 700;
}
tbody td { padding: 11px 14px; border-bottom: 1px solid rgba(91,138,247,0.05); vertical-align: middle; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: rgba(91,138,247,0.04); }

/* ── Badges ── */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 20px;
  font-size: .72rem;
  font-weight: 700;
  letter-spacing: .3px;
}
.badge-pass { background: rgba(52,211,153,.1); color: var(--green); border: 1px solid rgba(52,211,153,.2); }
.badge-fail { background: rgba(248,113,113,.1); color: var(--red); border: 1px solid rgba(248,113,113,.2); }
.badge-error { background: rgba(248,113,113,.08); color: #fca5a5; border: 1px solid rgba(248,113,113,.15); }
.badge-running { background: rgba(251,191,36,.1); color: var(--yellow); border: 1px solid rgba(251,191,36,.2); }
.badge-queued { background: rgba(71,85,105,.12); color: var(--muted); border: 1px solid var(--border); }
.badge-dash { color: var(--muted); font-size: .82rem; }

/* ── Savings bar ── */
.savings-bar { display: flex; align-items: center; gap: 8px; }
.bar-bg { flex: 1; height: 5px; background: var(--surface2); border-radius: 3px; overflow: hidden; max-width: 80px; }
.bar-fill { height: 100%; border-radius: 3px; transition: width .3s; }

/* ── Error details ── */
details summary { cursor: pointer; color: var(--red); font-size: .8rem; list-style: none; display: flex; align-items: center; gap: 4px; }
details summary::-webkit-details-marker { display: none; }
details[open] summary { margin-bottom: 6px; }
.error-text {
  font-family: 'JetBrains Mono', 'SF Mono', monospace;
  font-size: .75rem;
  background: rgba(248,113,113,.06);
  border: 1px solid rgba(248,113,113,.15);
  border-radius: 8px;
  padding: 10px 12px;
  color: #fca5a5;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  max-width: 320px;
}

/* ── Spinner ── */
@keyframes spin { to { transform: rotate(360deg); } }
.spinner {
  width: 14px; height: 14px;
  border: 2px solid rgba(251,191,36,.3);
  border-top-color: var(--yellow);
  border-radius: 50%;
  animation: spin .8s linear infinite;
  display: inline-block;
  vertical-align: middle;
}

/* ── Empty / no-match ── */
.empty-state { text-align: center; padding: 52px 24px; color: var(--muted); }
.empty-state svg { margin: 0 auto 12px; display: block; opacity: .2; }
.empty-state p { font-size: .9rem; }
.no-match { text-align: center; padding: 32px; color: var(--muted); font-size: .88rem; display: none; }

/* ── Links ── */
a { color: var(--accent); text-decoration: none; transition: color .15s; }
a:hover { color: var(--accent-2); text-decoration: underline; }

/* ── Advice panel ── */
.advice-panel { margin-top: 24px; }
.advice-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 24px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.3);
}
.advice-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
.advice-header-icon {
  width: 32px; height: 32px;
  border-radius: 8px;
  background: rgba(138,170,248,0.12);
  display: flex; align-items: center; justify-content: center;
  color: var(--accent-2);
  flex-shrink: 0;
}
.advice-title-text { font-size: .88rem; font-weight: 700; color: var(--text); }
.advice-meta { font-size: .75rem; color: var(--muted); margin-top: 2px; }
.advice-summary {
  font-size: .82rem;
  color: var(--text2);
  margin-bottom: 16px;
  line-height: 1.6;
  padding: 12px 16px;
  background: rgba(91,138,247,0.05);
  border-radius: 8px;
  border-left: 3px solid rgba(91,138,247,0.4);
}
.prescription {
  display: flex;
  gap: 14px;
  padding: 14px 0;
  border-bottom: 1px solid rgba(91,138,247,0.07);
}
.prescription:last-child { border-bottom: none; }
.prescription-rank {
  width: 28px; height: 28px;
  border-radius: 8px;
  background: rgba(91,138,247,0.1);
  color: var(--accent);
  font-size: .82rem;
  font-weight: 800;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}
.prescription-body { flex: 1; min-width: 0; }
.prescription-title { font-size: .88rem; font-weight: 600; color: var(--text); margin-bottom: 5px; }
.prescription-meta { display: flex; gap: 8px; flex-wrap: wrap; font-size: .73rem; color: var(--muted); margin-bottom: 6px; align-items: center; }
.prescription-meta .p-savings { color: var(--green); font-weight: 700; }
.prescription-desc { font-size: .8rem; color: var(--text2); line-height: 1.5; }
.p-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: .7rem; font-weight: 700; letter-spacing: .3px; }
.p-badge-dedup { background: rgba(96,165,250,.1); color: #93c5fd; }
.p-badge-context { background: rgba(138,170,248,.1); color: #93b4f8; }
.p-badge-schema { background: rgba(52,211,153,.1); color: #6ee7b7; }
.p-badge-replace { background: rgba(251,191,36,.1); color: #fcd34d; }
.p-badge-loop { background: rgba(248,113,113,.1); color: #fca5a5; }
.p-badge-cache { background: rgba(52,211,153,.1); color: #86efac; }

/* ── Code block ── */
.code-block {
  background: rgba(0,0,0,0.4);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 20px;
  font-family: 'JetBrains Mono', 'SF Mono', 'Fira Code', monospace;
  font-size: .82rem;
  line-height: 1.7;
  color: #94a3b8;
  overflow-x: auto;
  white-space: pre;
}
.code-block .kw { color: var(--accent-2); }
.code-block .st { color: #86efac; }
.code-block .cm { color: var(--muted); }

/* ── Nav icon buttons ── */
.nav-icon-btn {
  background: none; border: none; color: var(--text2); cursor: pointer;
  padding: 6px; border-radius: 6px; display: flex; align-items: center;
  transition: color .15s, background .15s;
}
.nav-icon-btn:hover { color: var(--text); background: var(--surface2); }

/* ── Modal ── */
.modal-overlay {
  display: none; position: fixed; inset: 0; z-index: 1000;
  background: rgba(0,0,0,.6); backdrop-filter: blur(4px);
  align-items: center; justify-content: center;
}
.modal-overlay.open { display: flex; }
.modal-panel {
  background: var(--surface); border: 1px solid var(--border-m);
  border-radius: 14px; width: 420px; max-width: calc(100vw - 32px);
  box-shadow: 0 24px 80px rgba(0,0,0,.5);
}
.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 18px 20px 14px; border-bottom: 1px solid var(--border);
}
.modal-title { font-weight: 600; font-size: 1rem; color: var(--text); }
.modal-close {
  background: none; border: none; color: var(--text2); cursor: pointer;
  padding: 4px; border-radius: 6px; display: flex; align-items: center;
  transition: color .15s;
}
.modal-close:hover { color: var(--text); }
.modal-body { padding: 18px 20px 22px; display: flex; flex-direction: column; gap: 18px; }
.setting-row {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
}
.setting-label { font-size: .9rem; font-weight: 500; color: var(--text); }
.setting-hint { font-size: .78rem; color: var(--text2); margin-top: 2px; }
/* Toggle switch */
.toggle { position: relative; display: inline-block; width: 40px; height: 22px; flex-shrink: 0; }
.toggle input { opacity: 0; width: 0; height: 0; }
.toggle-track {
  position: absolute; inset: 0; background: var(--surface3); border-radius: 11px;
  cursor: pointer; transition: background .2s;
}
.toggle-track::before {
  content: ''; position: absolute; width: 16px; height: 16px; border-radius: 50%;
  background: var(--text2); left: 3px; top: 3px; transition: transform .2s, background .2s;
}
.toggle input:checked + .toggle-track { background: var(--accent); }
.toggle input:checked + .toggle-track::before { transform: translateX(18px); background: #fff; }

/* Provider keys + buttons */
.btn-primary {
  background: var(--accent); color: #fff; border: none; border-radius: 8px;
  font-size: .85rem; font-weight: 600; cursor: pointer; transition: filter .15s;
}
.btn-primary:hover { filter: brightness(1.1); }
.btn-primary:disabled { background: var(--surface2); color: var(--muted); cursor: not-allowed; }
.btn-ghost {
  background: transparent; color: var(--text2); border: 1px solid var(--border);
  border-radius: 8px; font-size: .85rem; cursor: pointer; transition: color .15s, border-color .15s;
}
.btn-ghost:hover { color: var(--text); border-color: var(--border-m); }
.key-row { margin-bottom: 12px; }
.key-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 5px; }
.key-name { font-size: .85rem; font-weight: 600; color: var(--text); }
#provider-keys input {
  width: 100%; box-sizing: border-box; background: var(--surface2); color: var(--text);
  border: 1px solid var(--border); padding: 8px 10px; border-radius: 6px;
  font-size: .82rem; font-family: monospace;
}
#provider-keys input:focus { outline: none; border-color: var(--accent); }
.status-badge {
  font-size: .72rem; font-weight: 600; padding: 2px 9px; border-radius: 999px;
  background: var(--surface2); color: var(--muted); white-space: nowrap;
}
.status-badge.ok { background: rgba(52,199,123,0.15); color: #34c77b; }
.status-badge.off { background: rgba(248,113,113,0.13); color: #f87171; }
.status-badge.warn { background: rgba(245,180,80,0.14); color: #f5b450; }

/* Help modal */
.help-section { display: flex; flex-direction: column; gap: 6px; }
.help-heading { font-size: .88rem; font-weight: 600; color: var(--text); }
.help-text { font-size: .82rem; color: var(--text2); line-height: 1.55; }
.help-text code {
  background: var(--surface2); color: var(--accent); padding: 2px 6px;
  border-radius: 4px; font-size: .78rem; font-family: monospace;
}

/* ── Form hints ── */
.field-hint { font-size: .76rem; color: var(--muted); margin-top: 4px; }

/* ── Responsive ── */
@media (max-width: 1100px) {
  .kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .main-grid { grid-template-columns: 1fr; }
}
@media (max-width: 700px) {
  .kpi-grid { grid-template-columns: 1fr 1fr; gap: 10px; }
  .page { padding: 16px; }
  .nav { padding: 0 16px; }
  .plans-grid { grid-template-columns: 1fr !important; }
  .nav-links { display: none; }
}
@media (max-width: 440px) {
  .kpi-grid { grid-template-columns: 1fr; }
}
"""


def _build_cert_widget() -> str:
    """Build the certification summary widget for the main dashboard."""
    certs = _load_cert_history()

    if not certs:
        return """
  <div class="card" style="margin-top:4px">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px">
      <div style="display:flex;align-items:center;gap:14px">
        <div style="width:44px;height:44px;border-radius:10px;background:rgba(91,138,247,0.1);display:flex;align-items:center;justify-content:center;flex-shrink:0">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><path d="M12,2l3.09,6.26L22,9.27l-5,4.87 1.18,6.88L12,17.77l-6.18,3.25 1.18-6.88-5-4.87 6.91-1.01z"/></svg>
        </div>
        <div>
          <div style="font-size:.95rem;font-weight:600;color:var(--text)">RunCore Score™ — not yet certified</div>
          <div style="font-size:.82rem;color:var(--text2);margin-top:2px">Run a certification to get your score and share proof of savings with customers.</div>
        </div>
      </div>
      <div style="display:flex;gap:10px;align-items:center;flex-shrink:0">
        <code style="font-size:.78rem;background:var(--surface2);color:var(--accent);padding:6px 12px;border-radius:6px;white-space:nowrap">runcore certify --provider groq</code>
        <a href="/certification" style="background:linear-gradient(135deg,#5577f3,#4a6cf5);color:#fff;text-decoration:none;font-size:.83rem;font-weight:500;padding:8px 16px;border-radius:8px;white-space:nowrap">Open Certification →</a>
      </div>
    </div>
  </div>"""

    best = max(certs, key=lambda c: c.get("overall", 0))
    latest = certs[0]
    n_certified = sum(1 for c in certs if c.get("certified"))
    grade = best.get("grade", "?")
    score = best.get("overall", 0)
    certified = best.get("certified", False)
    grade_color = {"A+": "#22c55e", "A": "#22c55e", "B+": "#60a5fa", "B": "#6488f5", "C": "#fbbf24", "F": "#ef4444"}.get(grade, "#94a3b8")
    cert_bg = "rgba(34,197,94,0.1)" if certified else "rgba(248,113,113,0.1)"
    cert_color = "#22c55e" if certified else "#f87171"
    cert_text = "✓ RunCore Certified" if certified else "✗ Not Certified"

    dims = best.get("dimensions", [])
    cost_imp = next((d["improvement_pct"] for d in dims if "Cost" in d.get("name","")), 0)
    tok_imp  = next((d["improvement_pct"] for d in dims if "Token" in d.get("name","")), 0)
    ci = best.get("confidence_interval_95", [score, score])
    html_file = best.get("html_file", "")
    report_link = f'/certification/reports/{html_file}' if html_file else '/certification'

    # Mini dim bars
    dim_bars = ""
    for d in dims:
        sc = min(100, max(0, d.get("score", 0)))
        color = "#22c55e" if d.get("passed") else "#f59e0b"
        dim_bars += f"""<div style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:3px">
            <span style="font-size:.75rem;color:var(--text2)">{d.get('name','')}</span>
            <span style="font-size:.75rem;font-weight:600;color:{color}">+{d.get('improvement_pct',0):.1f}%</span>
          </div>
          <div style="background:var(--surface2);border-radius:3px;height:5px">
            <div style="width:{sc}%;height:100%;background:{color};border-radius:3px"></div>
          </div>
        </div>"""

    history_dots = ""
    for c in certs[:8]:
        s = c.get("overall", 0)
        col = "#22c55e" if c.get("certified") else "#f87171" if s < 40 else "#fbbf24"
        tip = f"Score {s:.0f} — {c.get('provider','?')} / {c.get('suite','?')} — {c.get('timestamp','')[:10]}"
        history_dots += f'<div title="{tip}" style="width:10px;height:10px;border-radius:50%;background:{col};cursor:pointer" onclick="window.location=\'/certification\'"></div>'

    return f"""
  <div class="card" style="margin-top:4px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;flex-wrap:wrap;gap:10px">
      <div class="card-title" style="margin:0">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12,2l3.09,6.26L22,9.27l-5,4.87 1.18,6.88L12,17.77l-6.18,3.25 1.18-6.88-5-4.87 6.91-1.01z"/></svg>
        RunCore Score™
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <div style="display:flex;gap:5px;align-items:center">{history_dots}</div>
        <a href="/certification" style="font-size:.78rem;color:var(--accent);text-decoration:none;padding:4px 10px;border:1px solid var(--border);border-radius:6px">ver tudo →</a>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:auto 1fr;gap:24px;align-items:start">
      <div style="text-align:center;min-width:110px">
        <div style="font-size:3rem;font-weight:800;line-height:1;color:{grade_color}">{score:.0f}</div>
        <div style="font-size:.85rem;font-weight:600;color:{grade_color};margin-top:2px">{grade}</div>
        <div style="margin-top:8px;padding:4px 10px;border-radius:100px;background:{cert_bg};display:inline-block">
          <span style="font-size:.72rem;font-weight:600;color:{cert_color}">{cert_text}</span>
        </div>
        <div style="font-size:.7rem;color:var(--muted);margin-top:6px">CI [{ci[0]:.0f}–{ci[1]:.0f}]</div>
        <div style="font-size:.72rem;color:var(--text2);margin-top:4px">{best.get('provider','?')} · {best.get('suite','?')}</div>
        <a href="{report_link}" style="display:inline-block;margin-top:10px;font-size:.75rem;color:var(--accent);text-decoration:none">↗ relatório completo</a>
      </div>
      <div>
        {dim_bars}
        <div style="display:flex;gap:16px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
          <div>
            <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">Cost savings</div>
            <div style="font-size:1.1rem;font-weight:700;color:var(--green)">+{cost_imp:.1f}%</div>
          </div>
          <div>
            <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">Token reduction</div>
            <div style="font-size:1.1rem;font-weight:700;color:var(--blue)">+{tok_imp:.1f}%</div>
          </div>
          <div>
            <div style="font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">Certified</div>
            <div style="font-size:1.1rem;font-weight:700;color:var(--yellow)">{n_certified}/{len(certs)}</div>
          </div>
          <div style="margin-left:auto;align-self:flex-end">
            <a href="/certification" style="background:linear-gradient(135deg,#5577f3,#4a6cf5);color:#fff;text-decoration:none;font-size:.78rem;font-weight:500;padding:7px 14px;border-radius:7px;white-space:nowrap">+ Nova certificação</a>
          </div>
        </div>
      </div>
    </div>
  </div>"""


def _build_advice_panel(runs_snapshot: dict) -> str:
    """Build the OptimizationAdvisor panel HTML from the most recent done run."""
    # Find the most recent run with advice data
    advice_run = None
    for run in sorted(runs_snapshot.values(), key=lambda r: r.get("finished_at", ""), reverse=True):
        if run.get("status") == "done" and run.get("advice"):
            advice_run = run
            break

    if not advice_run:
        return ""

    advice = advice_run["advice"]
    prescriptions = advice.get("prescriptions", [])
    if not prescriptions:
        return ""

    summary_html = f'<div class="advice-summary">{advice.get("summary", "")}</div>'

    presc_html = ""
    for i, p in enumerate(prescriptions[:5], 1):
        ptype = p.get("type", "")
        badge_label, badge_cls = _P_BADGE.get(ptype, ("Other", "p-badge-schema"))
        effort = p.get("effort", "low")
        effort_color = {"low": "var(--green)", "medium": "var(--yellow)", "high": "var(--red)"}.get(effort, "var(--muted)")
        presc_html += f"""
        <div class="prescription">
          <div class="prescription-rank">{i}</div>
          <div class="prescription-body">
            <div class="prescription-title">{p.get('title', '')}</div>
            <div class="prescription-meta">
              <span class="p-savings">~{p.get('estimated_savings_pct', 0):.1f}% savings</span>
              <span style="color:var(--border-m)">|</span>
              <span class="p-badge {badge_cls}">{badge_label}</span>
              <span style="color:var(--border-m)">|</span>
              <span style="color:{effort_color}">effort: {effort}</span>
              <span style="color:var(--border-m)">|</span>
              <span>confidence {p.get('confidence', 0)*100:.0f}%</span>
            </div>
            <div class="prescription-desc">{p.get('description', '')[:200]}</div>
          </div>
        </div>"""

    total_pct = advice.get("total_estimated_savings_pct", 0)
    n = advice.get("traces_analyzed", 0)
    agent = advice.get("agent_name", "")

    return f"""
  <div class="advice-panel">
    <div class="advice-card">
      <div class="advice-header">
        <div class="advice-header-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        </div>
        <div>
          <div class="advice-title-text">OptimizationAdvisor</div>
          <div class="advice-meta">{agent} &nbsp;·&nbsp; {n} traces analyzed &nbsp;·&nbsp; combined ~{total_pct:.1f}% estimated savings</div>
        </div>
      </div>
      {summary_html}
      {presc_html}
    </div>
  </div>"""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    with _lock:
        runs_snapshot = dict(_runs)

    # Load any persisted reports not in memory
    for p in sorted(_REPORTS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
        rid = p.stem
        if rid == "benchmark":
            continue
        if rid not in runs_snapshot:
            try:
                data = json.loads(p.read_text())
                runs_snapshot[rid] = {
                    "status": "done",
                    "result": data.get("result", "?"),
                    "cost_savings_pct": data.get("cost_savings_pct", 0),
                    "token_reduction_pct": data.get("token_reduction_pct", 0),
                    "tool_call_reduction_pct": data.get("tool_call_reduction_pct", 0),
                    "baseline_cost": data.get("baseline", {}).get("total_cost", 0),
                    "optimized_cost": data.get("optimized", {}).get("total_cost", 0),
                    "runs": data.get("runs", 0),
                    "json_path": str(p),
                    "html_path": str(p).replace(".json", ".html"),
                    "finished_at": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            except Exception:
                pass

    # Sort by finish time for chart
    done_runs = [
        r for r in runs_snapshot.values()
        if r.get("status") == "done" and r.get("finished_at")
    ]
    done_runs.sort(key=lambda r: r.get("finished_at", ""))

    chart_labels = json.dumps([r["finished_at"][11:19] for r in done_runs])
    chart_baseline = json.dumps([round(r.get("baseline_cost", 0), 5) for r in done_runs])
    chart_optimized = json.dumps([round(r.get("optimized_cost", 0), 5) for r in done_runs])
    chart_savings = json.dumps([round(r.get("cost_savings_pct", 0), 1) for r in done_runs])

    def _savings_bar(pct: float, target: float = 25.0) -> str:
        capped = min(pct, 100.0)
        color = "#4ade80" if pct >= target else ("#fbbf24" if pct >= target * 0.6 else "#f87171")
        return (
            f'<div class="savings-bar">'
            f'<span style="color:{color};font-weight:600;min-width:42px">{pct:.1f}%</span>'
            f'<div class="bar-bg"><div class="bar-fill" style="width:{capped}%;background:{color}"></div></div>'
            f'</div>'
        )

    rows = ""
    for rid, run in sorted(runs_snapshot.items(), key=lambda x: x[1].get("finished_at", x[0]), reverse=True):
        status = run.get("status", "?")
        result = run.get("result", "—")
        agent_label = run.get("agent", "—")
        finished = run.get("finished_at", "")[:10]
        time_str = run.get("finished_at", "")

        # Status badge
        if status == "done":
            status_badge = '<span class="badge badge-queued" style="background:rgba(74,222,128,.08);color:#86efac;border-color:rgba(74,222,128,.15)">done</span>'
        elif status == "running":
            status_badge = '<span class="badge badge-running"><span class="spinner" style="width:10px;height:10px"></span> running</span>'
        elif status == "queued":
            status_badge = '<span class="badge badge-queued">queued</span>'
        else:
            status_badge = '<span class="badge badge-error">error</span>'

        # Result badge
        if result == "PASS":
            result_badge = '<span class="badge badge-pass">✓ PASS</span>'
        elif result == "FAIL":
            result_badge = '<span class="badge badge-fail">✗ FAIL</span>'
        else:
            result_badge = '<span class="badge-dash">—</span>'

        # Savings columns
        cost_savings = run.get("cost_savings_pct", 0) or 0
        token_savings = run.get("token_reduction_pct", 0) or 0
        b_cost = run.get("baseline_cost", 0) or 0
        o_cost = run.get("optimized_cost", 0) or 0

        if status == "done":
            savings_cell = _savings_bar(cost_savings)
            token_cell = f'<span style="color:#60a5fa">{token_savings:.1f}%</span>'
            cost_cell = f'<span style="color:#64748b;font-family:monospace;font-size:.8rem">${b_cost:.5f}</span> <span style="color:#475569">→</span> <span style="color:#4ade80;font-family:monospace;font-size:.8rem">${o_cost:.5f}</span>'
            runs_cell = str(run.get("runs", "—"))
            report_cell = f'<a href="/reports/{rid}">view report</a>'
        elif status == "running":
            savings_cell = '<span style="color:#475569">—</span>'
            token_cell = '<span style="color:#475569">—</span>'
            cost_cell = '<span style="color:#475569">in progress…</span>'
            runs_cell = "—"
            report_cell = '<span style="color:#475569">—</span>'
        elif status == "error":
            error_msg = run.get("error", "unknown error")
            savings_cell = '<span style="color:#475569">—</span>'
            token_cell = '<span style="color:#475569">—</span>'
            cost_cell = '<span style="color:#475569">—</span>'
            runs_cell = "—"
            # Full error in expandable details
            import html as _html
            escaped = _html.escape(error_msg)
            report_cell = f'<details><summary>⚠ show error</summary><div class="error-text">{escaped}</div></details>'
        else:
            savings_cell = token_cell = cost_cell = '<span style="color:#475569">—</span>'
            runs_cell = "—"
            report_cell = '<span style="color:#475569">—</span>'

        # Run ID + time tooltip
        short_id = rid[:8]
        time_display = time_str[11:19] if len(time_str) > 19 else ""
        id_cell = f'<span style="font-family:monospace;font-size:.78rem;color:#475569" title="{rid}">{short_id}</span><br><span style="font-size:.72rem;color:#334155">{finished} {time_display}</span>'

        rows += f"""<tr data-result="{result}" data-agent="{agent_label}" data-status="{status}" data-date="{finished}">
          <td>{id_cell}</td>
          <td>{status_badge}</td>
          <td>{result_badge}</td>
          <td><span style="font-size:.8rem;color:#94a3b8">{agent_label}</span></td>
          <td>{savings_cell}</td>
          <td>{token_cell}</td>
          <td>{cost_cell}</td>
          <td style="color:#64748b">{runs_cell}</td>
          <td>{report_cell}</td>
        </tr>"""

    # KPI aggregates
    total_runs = len(done_runs)
    pass_runs = [r for r in done_runs if r.get("result") == "PASS"]
    avg_savings = (sum(r.get("cost_savings_pct", 0) for r in done_runs) / total_runs) if total_runs else 0
    best_savings = max((r.get("cost_savings_pct", 0) for r in done_runs), default=0)
    avg_tokens = (sum(r.get("token_reduction_pct", 0) for r in done_runs) / total_runs) if total_runs else 0
    pass_rate = (len(pass_runs) / total_runs * 100) if total_runs else 0
    running_count = sum(1 for r in runs_snapshot.values() if r.get("status") == "running")

    kpi_savings_color = "#4ade80" if avg_savings >= 25 else ("#fbbf24" if avg_savings >= 15 else "#f87171")
    kpi_pass_color = "#4ade80" if pass_rate >= 80 else ("#fbbf24" if pass_rate >= 50 else "#f87171")

    has_chart = "true" if done_runs else "false"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore — cost-control runtime for AI agents</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>{_DESIGN_CSS}</style>
</head>
<body>

<nav class="nav">
  <div class="nav-logo">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="url(#lg)" stroke-width="2.5"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#6488f5"/><stop offset="100%" stop-color="#8aaaf8"/></linearGradient></defs><polygon points="13,2 3,14 12,14 11,22 21,10 12,10"/></svg>
    RunCore
  </div>
  <div class="nav-links">
    <a href="/" class="nav-link active">Dashboard</a>
    <a href="/certification" class="nav-link">Certification</a>
    <a href="/leaderboard" class="nav-link">Leaderboard</a>
    <a href="/cloud/dashboard" class="nav-link">Cloud</a>
    <a href="/cloud/billing/plans" class="nav-link">Pricing</a>
  </div>
  <div class="nav-right">
    <div class="status-dot {'idle' if not running_count else ''}"></div>
    <span>{'idle' if not running_count else str(running_count) + ' running'}</span>
    <span style="color:var(--border-m)">·</span>
    <button class="nav-icon-btn" onclick="document.getElementById('help-modal').classList.add('open')" title="Help & Docs">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09,9a3,3,0,0,1,5.83,1c0,2-3,3-3,3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
    </button>
    <button class="nav-icon-btn" onclick="document.getElementById('settings-modal').classList.add('open')" title="Settings">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4,15a1.65,1.65,0,0,0,.33,1.82l.06.06a2,2,0,0,1,0,2.83,2,2,0,0,1-2.83,0l-.06-.06a1.65,1.65,0,0,0-1.82-.33,1.65,1.65,0,0,0-1,1.51V21a2,2,0,0,1-4,0v-.09A1.65,1.65,0,0,0,9,19.4a1.65,1.65,0,0,0-1.82.33l-.06.06a2,2,0,0,1-2.83-2.83l.06-.06A1.65,1.65,0,0,0,4.68,15a1.65,1.65,0,0,0-1.51-1H3a2,2,0,0,1,0-4h.09A1.65,1.65,0,0,0,4.6,9a1.65,1.65,0,0,0-.33-1.82l-.06-.06a2,2,0,0,1,2.83-2.83l.06.06A1.65,1.65,0,0,0,9,4.68a1.65,1.65,0,0,0,1-1.51V3a2,2,0,0,1,4,0v.09a1.65,1.65,0,0,0,1,1.51,1.65,1.65,0,0,0,1.82-.33l.06-.06a2,2,0,0,1,2.83,2.83l-.06.06A1.65,1.65,0,0,0,19.4,9a1.65,1.65,0,0,0,1.51,1H21a2,2,0,0,1,0,4h-.09A1.65,1.65,0,0,0,19.4,15z"/></svg>
    </button>
  </div>
</nav>

<!-- Settings modal -->
<div class="modal-overlay" id="settings-modal" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="modal-panel">
    <div class="modal-header">
      <span class="modal-title">Settings</span>
      <button class="modal-close" onclick="document.getElementById('settings-modal').classList.remove('open')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="modal-body">
      <div class="setting-row">
        <div>
          <div class="setting-label">Auto-refresh dashboard</div>
          <div class="setting-hint">Reload page automatically to show new results</div>
        </div>
        <label class="toggle">
          <input type="checkbox" id="s-autorefresh" checked onchange="saveSettings()">
          <span class="toggle-track"></span>
        </label>
      </div>
      <div class="setting-row">
        <div>
          <div class="setting-label">Refresh interval</div>
          <div class="setting-hint">How often to reload (seconds)</div>
        </div>
        <select id="s-interval" onchange="saveSettings()" style="background:var(--surface2);color:var(--text);border:1px solid var(--border);padding:6px 10px;border-radius:6px;font-size:.85rem">
          <option value="10">10 s</option>
          <option value="30" selected>30 s</option>
          <option value="60">60 s</option>
          <option value="120">2 min</option>
        </select>
      </div>
      <div class="setting-row">
        <div>
          <div class="setting-label">Default provider</div>
          <div class="setting-hint">Pre-select when running benchmarks</div>
        </div>
        <select id="s-provider" onchange="saveSettings()" style="background:var(--surface2);color:var(--text);border:1px solid var(--border);padding:6px 10px;border-radius:6px;font-size:.85rem">
          <option value="groq">Groq (free)</option>
          <option value="gemini">Gemini (free)</option>
          <option value="ollama">Ollama (local)</option>
        </select>
      </div>

      <div style="border-top:1px solid var(--border);margin:18px 0 14px"></div>
      <div class="setting-label" style="margin-bottom:4px">Provider API Keys</div>
      <div class="setting-hint" style="margin-bottom:12px">Paste your keys here — saved on the server, no terminal needed. Free tiers: <a href="https://console.groq.com" target="_blank" style="color:var(--accent)">Groq</a> · <a href="https://aistudio.google.com" target="_blank" style="color:var(--accent)">Gemini</a>.</div>

      <div id="provider-keys">
        <div class="key-row" data-provider="groq">
          <div class="key-head"><span class="key-name">Groq</span><span class="status-badge" id="badge-groq">checking…</span></div>
          <input type="password" id="key-groq" placeholder="gsk_..." autocomplete="off">
        </div>
        <div class="key-row" data-provider="gemini">
          <div class="key-head"><span class="key-name">Gemini</span><span class="status-badge" id="badge-gemini">checking…</span></div>
          <input type="password" id="key-gemini" placeholder="AIza..." autocomplete="off">
        </div>
        <div class="key-row" data-provider="ollama">
          <div class="key-head"><span class="key-name">Ollama (local)</span><span class="status-badge" id="badge-ollama">checking…</span></div>
          <input type="text" id="key-ollama" placeholder="localhost:11434 (optional)" autocomplete="off">
        </div>
        <div class="key-row" data-provider="openai">
          <div class="key-head"><span class="key-name">OpenAI <span style="opacity:.5;font-weight:400">(optional)</span></span><span class="status-badge" id="badge-openai">checking…</span></div>
          <input type="password" id="key-openai" placeholder="sk-..." autocomplete="off">
        </div>
        <div class="key-row" data-provider="anthropic">
          <div class="key-head"><span class="key-name">Anthropic <span style="opacity:.5;font-weight:400">(optional)</span></span><span class="status-badge" id="badge-anthropic">checking…</span></div>
          <input type="password" id="key-anthropic" placeholder="sk-ant-..." autocomplete="off">
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-top:6px">
        <button class="btn-primary" id="save-keys-btn" onclick="saveKeys()" style="padding:8px 16px">Save keys</button>
        <button class="btn-ghost" onclick="refreshProviderStatus()" style="padding:8px 12px">Re-check</button>
        <span id="keys-msg" style="font-size:.8rem;color:var(--muted)"></span>
      </div>

      <div style="border-top:1px solid var(--border);margin:18px 0 14px"></div>
      <div class="setting-row">
        <div>
          <div class="setting-label">Run unit tests</div>
          <div class="setting-hint">Validate the engine — runs offline, no API key needed</div>
        </div>
        <button class="btn-primary" id="run-tests-btn" onclick="runTests()" style="padding:8px 16px">Run tests</button>
      </div>
      <div id="tests-result" style="display:none;margin-top:10px;font-size:.82rem"></div>
    </div>
  </div>
</div>

<!-- Help modal -->
<div class="modal-overlay" id="help-modal" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="modal-panel">
    <div class="modal-header">
      <span class="modal-title">Help & Docs</span>
      <button class="modal-close" onclick="document.getElementById('help-modal').classList.remove('open')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="modal-body">
      <div class="help-section">
        <div class="help-heading">Running a Benchmark</div>
        <div class="help-text">Select an agent type, set how many runs per task, and paste tasks (one per line). RunCore runs each task twice — once as baseline, once with optimizations — and measures real cost and token savings.</div>
      </div>
      <div class="help-section">
        <div class="help-heading">Reading Results</div>
        <div class="help-text"><b>Cost savings %</b> — how much cheaper the optimized run was vs baseline.<br><b>Token reduction %</b> — fewer tokens sent to the LLM.<br><b>PASS</b> means cost savings exceeded your target threshold.</div>
      </div>
      <div class="help-section">
        <div class="help-heading">Free Providers — set keys in Settings ⚙</div>
        <div class="help-text">Open <b>Settings</b> (gear icon) and paste your keys — no terminal needed.<br><b>Groq</b> — free key at console.groq.com.<br><b>Gemini</b> — free key at aistudio.google.com.<br><b>Ollama</b> — run the Ollama app locally (no key).<br>A green <b>ready</b> badge means the provider is good to go.</div>
      </div>
      <div class="help-section">
        <div class="help-heading">Validate the engine</div>
        <div class="help-text">In <b>Settings ⚙</b>, click <b>Run tests</b> to run the full offline test suite and confirm everything works — all from the dashboard.</div>
      </div>
    </div>
  </div>
</div>

<div class="page">

  <!-- KPI strip -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(91,138,247,0.1);color:var(--accent)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>
      </div>
      <div>
        <div class="kpi-label">Total Runs</div>
        <div class="kpi-value" style="color:var(--accent)">{total_runs}</div>
        <div class="kpi-sub">{len(pass_runs)} passed · {total_runs - len(pass_runs)} failed/error</div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(52,211,153,0.1);color:var(--green)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23,6 13.5,15.5 8.5,10.5 1,18"/><polyline points="17,6 23,6 23,12"/></svg>
      </div>
      <div>
        <div class="kpi-label">Avg Cost Savings</div>
        <div class="kpi-value" style="color:{kpi_savings_color}">{avg_savings:.1f}%</div>
        <div class="kpi-sub">target ≥25% · best {best_savings:.1f}%</div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(96,165,250,0.1);color:var(--blue)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12,6 12,12 16,14"/></svg>
      </div>
      <div>
        <div class="kpi-label">Avg Token Reduction</div>
        <div class="kpi-value" style="color:var(--blue)">{avg_tokens:.1f}%</div>
        <div class="kpi-sub">fewer tokens sent to LLM</div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(52,211,153,0.1);color:var(--green)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22,4 12,14.01 9,11.01"/></svg>
      </div>
      <div>
        <div class="kpi-label">Pass Rate</div>
        <div class="kpi-value" style="color:{kpi_pass_color}">{pass_rate:.0f}%</div>
        <div class="kpi-sub">{len(pass_runs)} of {total_runs} runs met target</div>
      </div>
    </div>
  </div>

  <!-- Main grid: form + charts -->
  <div class="main-grid">

    <!-- Form -->
    <div class="card">
      <div class="card-title">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5,3 19,12 5,21"/></svg>
        Run Benchmark
      </div>
      <form id="bform">
        <div class="field">
          <label>Agent type</label>
          <select id="agent">
            <option value="support">support — customer service</option>
            <option value="research">research — web research</option>
            <option value="coding">coding — bug fixing</option>
          </select>
          <div class="field-hint">Determines which task suite and tool set to use</div>
        </div>
        <div class="form-row">
          <div class="field">
            <label>Runs per task</label>
            <input type="number" id="runs" value="3" min="1" max="100" placeholder="3">
            <div class="field-hint">3–10 recommended for free providers</div>
          </div>
          <div class="field">
            <label>Savings target %</label>
            <input type="number" id="target" value="25" min="1" max="90" placeholder="25">
            <div class="field-hint">PASS if actual savings ≥ this</div>
          </div>
        </div>
        <div class="field">
          <label>Tasks <span style="font-weight:400;color:var(--text2)">(one per line)</span></label>
          <textarea id="tasks" placeholder="Refund invoice #1001 for customer@example.com&#10;Check order status for john@example.com">Refund invoice #1001 for customer@example.com
Check order status for john@example.com</textarea>
          <div class="field-hint">Each line is a separate task — RunCore runs each with and without optimizations</div>
        </div>
        <button class="btn-run" type="submit" id="run-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5,3 19,12 5,21"/></svg>
          Run Benchmark
        </button>
        <div class="run-msg" id="run-msg"></div>
        <div class="progress-wrap" id="live-progress">
          <div class="progress-header">
            <span id="live-phase">Initializing…</span>
            <span id="live-pct">0%</span>
          </div>
          <div class="progress-track">
            <div class="progress-fill" id="live-bar"></div>
          </div>
        </div>
      </form>
    </div>

    <!-- Charts stacked -->
    <div class="charts-col">
      <div class="card" style="flex:1">
        <div class="card-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22,12 18,12 15,21 9,3 6,12 2,12"/></svg>
          Cost per run — Baseline vs Optimized
        </div>
        {'<div class="chart-wrap"><canvas id="costChart"></canvas></div>' if done_runs else '<div class="chart-empty"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><polyline points="3,9 9,9 12,6 16,15 19,12"/></svg><span>Run a benchmark to see trends</span></div>'}
      </div>
      <div class="card" style="flex:1">
        <div class="card-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          Cost savings % — target line at 25%
        </div>
        {'<div class="chart-wrap"><canvas id="savingsChart"></canvas></div>' if done_runs else '<div class="chart-empty"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="15" x2="9" y2="17"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="15" y1="8" x2="15" y2="17"/></svg><span>Run a benchmark to see savings</span></div>'}
      </div>
    </div>
  </div>

  <!-- History -->
  <div class="card">
    <div class="card-title" style="margin-bottom:0">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12,6 12,12 16,14"/></svg>
      Benchmark History
    </div>

    <div class="filters" style="margin-top:16px">
      <div class="filter-group">
        <label>Result</label>
        <select id="f-result">
          <option value="">All results</option>
          <option value="PASS">PASS</option>
          <option value="FAIL">FAIL</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Agent</label>
        <select id="f-agent">
          <option value="">All agents</option>
          <option value="support">support</option>
          <option value="research">research</option>
          <option value="coding">coding</option>
          <option value="real_support">real_support</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Status</label>
        <select id="f-status">
          <option value="">All statuses</option>
          <option value="done">done</option>
          <option value="error">error</option>
          <option value="running">running</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Date</label>
        <input type="date" id="f-date">
      </div>
      <button class="btn-reset" onclick="resetFilters()">Reset</button>
      <span class="f-count" id="f-count"></span>
    </div>

    <table>
      <thead>
        <tr>
          <th>Run</th>
          <th>Status</th>
          <th>Result</th>
          <th>Agent</th>
          <th>Cost savings</th>
          <th>Tokens saved</th>
          <th>Baseline → Optimized</th>
          <th>Runs</th>
          <th>Report</th>
        </tr>
      </thead>
      <tbody id="history-body">{rows}</tbody>
    </table>
    <div class="no-match" id="no-match">No runs match the current filters.</div>
    {'' if rows else '<div class="empty-state"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg><p>No benchmark runs yet — use the form above to start one.</p></div>'}
  </div>

  {_build_advice_panel(runs_snapshot)}

  {_build_cert_widget()}

</div><!-- /page -->

<script>
const hasData = {has_chart};
const labels  = {chart_labels};
const baseline  = {chart_baseline};
const optimized = {chart_optimized};
const savings   = {chart_savings};

const GRID = {{ color: 'rgba(91,138,247,0.06)' }};
const TICK = {{ color: '#475569', size: 11 }};

if (hasData && labels.length > 0) {{
  new Chart(document.getElementById('costChart'), {{
    type: 'line',
    data: {{
      labels,
      datasets: [
        {{ label: 'Baseline', data: baseline, borderColor: '#f87171', backgroundColor: 'rgba(248,113,113,0.06)', tension: 0.35, pointRadius: 4, pointHoverRadius: 6, fill: true }},
        {{ label: 'Optimized', data: optimized, borderColor: '#34d399', backgroundColor: 'rgba(52,211,153,0.06)', tension: 0.35, pointRadius: 4, pointHoverRadius: 6, fill: true }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '#94a3b8', usePointStyle: true, pointStyle: 'circle', padding: 20, font: {{ size: 11 }} }} }},
        tooltip: {{
          backgroundColor: '#0c1428', borderColor: 'rgba(91,138,247,0.25)', borderWidth: 1,
          titleColor: '#f1f5f9', bodyColor: '#94a3b8', padding: 12,
          callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: $${{ctx.parsed.y.toFixed(5)}}` }}
        }}
      }},
      scales: {{
        x: {{ ticks: TICK, grid: GRID }},
        y: {{ ticks: {{ ...TICK, callback: v => '$' + v.toFixed(4) }}, grid: GRID, beginAtZero: false }}
      }}
    }}
  }});

  new Chart(document.getElementById('savingsChart'), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [{{
        label: 'Cost savings %',
        data: savings,
        backgroundColor: savings.map(v => v >= 25 ? 'rgba(52,211,153,0.65)' : v >= 15 ? 'rgba(251,191,36,0.65)' : 'rgba(248,113,113,0.65)'),
        borderRadius: 6,
        borderSkipped: false,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ labels: {{ color: '#94a3b8', usePointStyle: true, pointStyle: 'circle', padding: 20, font: {{ size: 11 }} }} }},
        tooltip: {{
          backgroundColor: '#0c1428', borderColor: 'rgba(91,138,247,0.25)', borderWidth: 1,
          titleColor: '#f1f5f9', bodyColor: '#94a3b8', padding: 12,
          callbacks: {{ label: ctx => ` Savings: ${{ctx.parsed.y.toFixed(1)}}%` }}
        }},
        annotation: {{ annotations: {{ target: {{
          type: 'line', yMin: 25, yMax: 25,
          borderColor: 'rgba(100,140,245,0.5)', borderWidth: 1.5, borderDash: [5,4],
          label: {{ content: 'target 25%', enabled: true, color: '#8aaaf8', font: {{ size: 10 }}, position: 'end' }}
        }} }} }}
      }},
      scales: {{
        x: {{ ticks: TICK, grid: GRID }},
        y: {{ ticks: {{ ...TICK, callback: v => v + '%' }}, grid: GRID, min: 0 }}
      }}
    }}
  }});
}}

// -- Filters --
function applyFilters() {{
  const fResult = document.getElementById('f-result').value;
  const fAgent  = document.getElementById('f-agent').value;
  const fStatus = document.getElementById('f-status').value;
  const fDate   = document.getElementById('f-date').value;
  const rows = [...document.querySelectorAll('#history-body tr[data-result]')];
  let visible = 0;
  rows.forEach(r => {{
    const show =
      (!fResult || r.dataset.result === fResult) &&
      (!fAgent  || r.dataset.agent  === fAgent) &&
      (!fStatus || r.dataset.status === fStatus) &&
      (!fDate   || r.dataset.date   === fDate);
    r.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  const active = fResult || fAgent || fStatus || fDate;
  document.getElementById('f-count').textContent = active ? visible + ' of ' + rows.length + ' shown' : '';
  document.getElementById('no-match').style.display = (active && visible === 0) ? 'block' : 'none';
}}

function resetFilters() {{
  ['f-result','f-agent','f-status','f-date'].forEach(id => document.getElementById(id).value = '');
  applyFilters();
}}

['f-result','f-agent','f-status','f-date'].forEach(id =>
  document.getElementById(id)?.addEventListener('change', applyFilters)
);
applyFilters();

// -- Form submit with SSE live progress --
document.getElementById('bform').addEventListener('submit', async e => {{
  e.preventDefault();
  const btn = document.getElementById('run-btn');
  const msg = document.getElementById('run-msg');
  const progress = document.getElementById('live-progress');
  const barEl = document.getElementById('live-bar');
  const phaseEl = document.getElementById('live-phase');
  const pctEl = document.getElementById('live-pct');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Starting…';
  msg.textContent = '';
  msg.className = 'run-msg';
  try {{
    const tasks = document.getElementById('tasks').value.split('\\n').map(s=>s.trim()).filter(Boolean);
    const body = {{
      agent: document.getElementById('agent').value,
      tasks,
      runs_per_task: parseInt(document.getElementById('runs').value),
      config: {{ cost_reduction_target: parseInt(document.getElementById('target').value) / 100 }}
    }};
    const r = await fetch('/benchmark', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(body) }});
    const d = await r.json();
    const runId = d.run_id;

    // Show live progress bar
    progress.style.display = 'block';
    btn.innerHTML = '<span class="spinner"></span> Running…';

    const phaseLabels = {{ baseline: 'Running baseline…', optimized: 'Running optimized…', analysis: 'Analyzing results…' }};

    const es = new EventSource(`/runs/${{runId}}/stream`);
    es.addEventListener('status', ev => {{
      const data = JSON.parse(ev.data);
      const pct = data.pct || 0;
      barEl.style.width = pct + '%';
      pctEl.textContent = pct + '%';
      phaseEl.textContent = phaseLabels[data.phase] || 'Running…';
    }});
    es.addEventListener('done', ev => {{
      es.close();
      const data = JSON.parse(ev.data);
      barEl.style.width = '100%';
      barEl.classList.add('done');
      pctEl.textContent = '100%';
      phaseEl.textContent = `Done — ${{data.result}} · ${{data.cost_savings_pct?.toFixed(1)}}% savings`;
      msg.textContent = `✓ Run ${{runId.substring(0,8)}} complete · ${{data.prescriptions}} optimization tip${{data.prescriptions !== 1 ? 's' : ''}} found`;
      setTimeout(() => location.reload(), 2000);
    }});
    es.addEventListener('error', ev => {{
      es.close();
      try {{
        const data = JSON.parse(ev.data);
        msg.textContent = '✗ ' + data.error;
      }} catch(_) {{ msg.textContent = '✗ Run failed'; }}
      msg.className = 'run-msg err';
      btn.disabled = false;
      btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5,3 19,12 5,21"/></svg> Run Benchmark';
    }});
  }} catch(err) {{
    msg.textContent = '✗ ' + err.message;
    msg.className = 'run-msg err';
    btn.disabled = false;
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5,3 19,12 5,21"/></svg> Run Benchmark';
  }}
}});

// -- Settings persistence --
function loadSettings() {{
  try {{ return JSON.parse(localStorage.getItem('rc_settings') || '{{}}'); }} catch {{ return {{}}; }}
}}
function saveSettings() {{
  const s = {{
    autorefresh: document.getElementById('s-autorefresh').checked,
    interval: parseInt(document.getElementById('s-interval').value),
    provider: document.getElementById('s-provider').value,
  }};
  localStorage.setItem('rc_settings', JSON.stringify(s));
  restartRefreshTimer();
}}
(function applySettings() {{
  const s = loadSettings();
  if (s.autorefresh === false) document.getElementById('s-autorefresh').checked = false;
  if (s.interval) {{ const el = document.getElementById('s-interval'); if (el) el.value = s.interval; }}
  if (s.provider) {{ const el = document.getElementById('s-provider'); if (el) el.value = s.provider; }}
}})();

// -- Provider keys + status --
function _setBadge(provider, info) {{
  const el = document.getElementById('badge-' + provider);
  if (!el) return;
  el.classList.remove('ok','off','warn');
  if (info.available) {{ el.classList.add('ok'); el.textContent = 'ready'; }}
  else if (info.has_key) {{ el.classList.add('warn'); el.textContent = 'check'; }}
  else {{ el.classList.add('off'); el.textContent = 'no key'; }}
  el.title = info.detail || '';
  const inp = document.getElementById('key-' + provider);
  if (inp && info.masked && !inp.value) inp.placeholder = info.masked;
}}
async function refreshProviderStatus() {{
  try {{
    const r = await fetch('/settings/status');
    const d = await r.json();
    Object.entries(d.providers || {{}}).forEach(([p, info]) => _setBadge(p, info));
  }} catch (e) {{}}
}}
async function saveKeys() {{
  const btn = document.getElementById('save-keys-btn');
  const msg = document.getElementById('keys-msg');
  const keys = {{}};
  ['groq','gemini','ollama','openai','anthropic'].forEach(p => {{
    const v = document.getElementById('key-' + p).value.trim();
    if (v) keys[p] = v;
  }});
  if (Object.keys(keys).length === 0) {{ msg.textContent = 'Nothing to save — fields are empty.'; return; }}
  btn.disabled = true; btn.textContent = 'Saving…'; msg.textContent = '';
  try {{
    const r = await fetch('/settings/keys', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ keys }}),
    }});
    const d = await r.json();
    Object.entries(d.providers || {{}}).forEach(([p, info]) => _setBadge(p, info));
    ['groq','gemini','ollama','openai','anthropic'].forEach(p => document.getElementById('key-' + p).value = '');
    msg.textContent = '✓ Saved';
  }} catch (e) {{ msg.textContent = '✗ ' + e.message; }}
  btn.disabled = false; btn.textContent = 'Save keys';
}}
async function runTests() {{
  const btn = document.getElementById('run-tests-btn');
  const out = document.getElementById('tests-result');
  btn.disabled = true; btn.textContent = 'Running…';
  out.style.display = 'block';
  out.innerHTML = '<span style="color:var(--muted)">Running unit tests… (a few seconds)</span>';
  try {{
    const r = await fetch('/tests/run', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ target: 'tests/unit/' }}),
    }});
    const d = await r.json();
    const color = d.ok ? '#34c77b' : '#f87171';
    const icon = d.ok ? '✓' : '✗';
    out.innerHTML =
      '<div style="color:' + color + ';font-weight:600;margin-bottom:6px">' + icon + ' ' +
      d.passed + ' passed · ' + d.failed + ' failed</div>' +
      '<div style="color:var(--muted);font-size:.78rem;margin-bottom:6px">' + (d.summary || '') + '</div>' +
      '<pre style="background:var(--surface2);padding:10px;border-radius:6px;max-height:200px;overflow:auto;font-size:.72rem;white-space:pre-wrap">' +
      (d.output || '').replace(/</g,'&lt;') + '</pre>';
  }} catch (e) {{ out.innerHTML = '<span style="color:#f87171">✗ ' + e.message + '</span>'; }}
  btn.disabled = false; btn.textContent = 'Run tests';
}}
// Load provider status when the settings modal first opens.
document.getElementById('settings-modal').addEventListener('click', function() {{}}, {{ once: true }});
refreshProviderStatus();

// -- Auto refresh --
let _liveRunning = false;
let _refreshTimer = null;
document.getElementById('bform').addEventListener('submit', () => {{ _liveRunning = true; }});
function restartRefreshTimer() {{
  if (_refreshTimer) clearTimeout(_refreshTimer);
  const s = loadSettings();
  if (s.autorefresh === false) return;
  const ms = (s.interval || 30) * 1000;
  _refreshTimer = setTimeout(() => {{ if (!_liveRunning) location.reload(); }}, ms);
}}
restartRefreshTimer();
</script>
</body>
</html>"""


@app.post("/benchmark")
async def start_benchmark(req: BenchmarkRequest, background_tasks: BackgroundTasks) -> dict:
    run_id = str(uuid.uuid4())
    with _lock:
        _runs[run_id] = {
            "status": "queued",
            "agent": req.agent,
            "tasks": req.tasks,
            "runs_per_task": req.runs_per_task,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
    background_tasks.add_task(_run_benchmark, run_id, req)
    return {"run_id": run_id, "status": "queued"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.11.0"}


@app.get("/status")
def status() -> dict:
    with _lock:
        return {
            "total_runs": len(_runs),
            "running": sum(1 for r in _runs.values() if r["status"] == "running"),
            "done": sum(1 for r in _runs.values() if r["status"] == "done"),
            "error": sum(1 for r in _runs.values() if r["status"] == "error"),
            "runs": {rid: r.get("status") for rid, r in _runs.items()},
        }


@app.get("/reports")
def list_reports() -> dict:
    reports = []
    for p in sorted(_REPORTS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
            reports.append({
                "id": p.stem,
                "result": data.get("result"),
                "cost_savings_pct": data.get("cost_savings_pct"),
                "runs": data.get("runs"),
            })
        except Exception:
            pass
    return {"reports": reports}


@app.get("/reports/{run_id}", response_class=HTMLResponse)
def view_report(run_id: str) -> str:
    html_path = _REPORTS_DIR / f"{run_id}.html"
    json_path = _REPORTS_DIR / f"{run_id}.json"

    if html_path.exists():
        return html_path.read_text()
    if json_path.exists():
        from runcore.core.models import BenchmarkResult
        from runcore.reports.generator import ReportGenerator
        data = json.loads(json_path.read_text())
        result = BenchmarkResult.model_validate(data)
        return ReportGenerator().generate_html(result)
    raise HTTPException(status_code=404, detail=f"Report {run_id} not found")


@app.get("/reports/{run_id}/json")
def report_json(run_id: str) -> JSONResponse:
    json_path = _REPORTS_DIR / f"{run_id}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"Report {run_id} not found")
    return JSONResponse(json.loads(json_path.read_text()))


# ---------------------------------------------------------------------------
# SSE streaming — live progress for a running benchmark
# ---------------------------------------------------------------------------

@app.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request):
    """Server-Sent Events endpoint — streams live benchmark progress.

    Connect with::

        const es = new EventSource(`/runs/${runId}/stream`);
        es.addEventListener('status', e => { const d = JSON.parse(e.data); ... });
        es.addEventListener('done', e => { es.close(); });
    """
    q: queue.Queue = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_queues.setdefault(run_id, []).append(q)

    async def event_generator():
        try:
            with _lock:
                run = _runs.get(run_id, {})
            current_status = run.get("status", "unknown")

            # If run already finished before client connected, send final state immediately
            if current_status in ("done", "error"):
                payload = {
                    "status": current_status,
                    "pct": 100,
                    "savings": run.get("savings"),
                    "token_reduction": run.get("token_reduction"),
                    "report_url": run.get("report_url"),
                    "error": run.get("error"),
                }
                yield f"event: {current_status}\ndata: {json.dumps(payload)}\n\n"
                return

            yield f"event: status\ndata: {json.dumps({'status': current_status, 'pct': 0})}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = q.get(timeout=0.5)
                    yield msg
                    if '"status": "done"' in msg or '"status": "error"' in msg:
                        break
                except queue.Empty:
                    # Check if run finished while we were waiting (race condition guard)
                    with _lock:
                        run = _runs.get(run_id, {})
                    if run.get("status") in ("done", "error"):
                        payload = {"status": run["status"], "pct": 100,
                                   "savings": run.get("savings"),
                                   "report_url": run.get("report_url")}
                        yield f"event: {run['status']}\ndata: {json.dumps(payload)}\n\n"
                        break
                    yield ": ping\n\n"
        finally:
            with _sse_lock:
                listeners = _sse_queues.get(run_id, [])
                if q in listeners:
                    listeners.remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# OptimizationAdvisor endpoint
# ---------------------------------------------------------------------------

@app.get("/runs/{run_id}/advice")
def get_advice(run_id: str) -> JSONResponse:
    """Return the OptimizationAdvisor report for a completed run."""
    # Try memory first
    with _lock:
        run = _runs.get(run_id)
        if run and "advice" in run:
            return JSONResponse(run["advice"])

    # Fall back to file
    advice_path = _REPORTS_DIR / f"{run_id}.advice.json"
    if advice_path.exists():
        return JSONResponse(json.loads(advice_path.read_text()))

    raise HTTPException(status_code=404, detail=f"Advice for run {run_id} not found")


@app.post("/advice")
async def analyze_traces(request: Request) -> JSONResponse:
    """Run the OptimizationAdvisor on uploaded ATIR traces.

    Accepts a JSON body: ``{"traces": [<ATIRTrace>, ...], "agent_name": "..."}``

    Returns an OptimizationReport with ranked prescriptions.
    """
    from runcore.advisor import OptimizationAdvisor
    from runcore.atir.converter import from_dict

    body = await request.json()
    raw_traces = body.get("traces", [])
    agent_name = body.get("agent_name")

    if not raw_traces:
        raise HTTPException(status_code=400, detail="No traces provided")

    try:
        atir_traces = [from_dict(t) for t in raw_traces]
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid ATIR trace: {exc}")

    advisor = OptimizationAdvisor()
    report = advisor.analyze(atir_traces, agent_name=agent_name)
    return JSONResponse(report.to_dict())


# ---------------------------------------------------------------------------
# Provider / config head-to-head comparison
# ---------------------------------------------------------------------------

class CompareRequest(BaseModel):
    agent: str = "support"
    tasks: list[str] = ["Refund invoice #1001 for customer@example.com"]
    runs_per_task: int = 5
    config_a: dict[str, Any] = {}
    config_b: dict[str, Any] = {}
    label_a: str = "Config A"
    label_b: str = "Config B"


@app.post("/compare")
async def compare_configs(req: CompareRequest, background_tasks: BackgroundTasks) -> dict:
    """Run two benchmark configs head-to-head and return a comparison report.

    Heavy computation is offloaded to a thread pool so the event loop is not blocked.
    """
    import asyncio
    loop = asyncio.get_event_loop()

    def _run_comparison():
        factory = SimulatedAgentFactory()
        agent = factory.create(req.agent)
        runner = BenchmarkRunner()
        valid_fields = OptimizationConfig.model_fields
        config_a = OptimizationConfig(**{k: v for k, v in req.config_a.items() if k in valid_fields})
        config_b = OptimizationConfig(**{k: v for k, v in req.config_b.items() if k in valid_fields})
        baseline = runner.run_baseline(agent, req.tasks, runs_per_task=req.runs_per_task)
        traces_a = runner.run_optimized(agent, req.tasks, config_a, runs_per_task=req.runs_per_task, baseline_traces=baseline)
        traces_b = runner.run_optimized(agent, req.tasks, config_b, runs_per_task=req.runs_per_task, baseline_traces=baseline)
        bm = calculate_metrics(baseline)
        m_a = calculate_metrics(traces_a)
        m_b = calculate_metrics(traces_b)
        cmp = BenchmarkComparison()
        result_a = cmp.compare(bm, m_a, config_a, baseline, traces_a)
        result_b = cmp.compare(bm, m_b, config_b, baseline, traces_b)
        winner = req.label_a if m_a.cost_per_successful_task <= m_b.cost_per_successful_task else req.label_b
        return {
            "winner": winner,
            "metric": "cost_per_successful_task",
            "configs": {
                req.label_a: {
                    "cost_savings_pct": result_a.cost_savings_pct,
                    "token_reduction_pct": result_a.token_reduction_pct,
                    "avg_cost": m_a.avg_cost,
                    "cost_per_successful_task": m_a.cost_per_successful_task,
                    "success_rate": m_a.success_rate,
                    "avg_quality": m_a.avg_quality_score,
                    "result": result_a.result,
                },
                req.label_b: {
                    "cost_savings_pct": result_b.cost_savings_pct,
                    "token_reduction_pct": result_b.token_reduction_pct,
                    "avg_cost": m_b.avg_cost,
                    "cost_per_successful_task": m_b.cost_per_successful_task,
                    "success_rate": m_b.success_rate,
                    "avg_quality": m_b.avg_quality_score,
                    "result": result_b.result,
                },
                "baseline": {
                    "avg_cost": bm.avg_cost,
                    "cost_per_successful_task": bm.cost_per_successful_task,
                    "success_rate": bm.success_rate,
                },
            },
        }

    return await loop.run_in_executor(None, _run_comparison)


# ===========================================================================
# Certification page + API
# ===========================================================================

def _load_cert_history() -> list[dict]:
    """Load all saved certification JSON files, newest first."""
    try:
        from benchmarks.certification import RESULTS_DIR
        cert_dir = RESULTS_DIR / "certifications"
        if not cert_dir.exists():
            return []
        certs = []
        for f in sorted(cert_dir.glob("*.json"), reverse=True)[:20]:
            try:
                import json as _j
                data = _j.loads(f.read_text())
                data["html_file"] = f.with_suffix(".html").name
                certs.append(data)
            except Exception:
                pass
        return certs
    except Exception:
        return []


@app.get("/certification", response_class=HTMLResponse)
def certification_page() -> str:
    certs = _load_cert_history()

    def _grade_color(grade: str) -> str:
        return {"A+": "#22c55e", "A": "#22c55e", "B+": "#60a5fa", "B": "#6488f5",
                "C": "#fbbf24", "F": "#ef4444"}.get(grade, "#94a3b8")

    def _cert_rows() -> str:
        if not certs:
            return """<tr><td colspan="8" style="text-align:center;padding:40px;color:var(--text2)">
              No certifications yet — run one below
            </td></tr>"""
        rows = []
        for c in certs:
            grade = c.get("grade", "?")
            score = c.get("overall", 0)
            certified = c.get("certified", False)
            ts = c.get("timestamp", "")[:15].replace("_", " ")
            dims = c.get("dimensions", [])
            cost_imp = next((d["improvement_pct"] for d in dims if "Cost" in d["name"]), 0)
            tok_imp  = next((d["improvement_pct"] for d in dims if "Token" in d["name"]), 0)
            badge = ('<span style="color:#22c55e;font-size:.75rem;font-weight:600">✓ Certified</span>'
                     if certified else
                     '<span style="color:#f87171;font-size:.75rem;font-weight:600">✗ Not certified</span>')
            html_file = c.get("html_file", "")
            view_link = f'<a href="/certification/reports/{html_file}" style="color:var(--accent);font-size:.82rem">view</a>' if html_file else "—"
            rows.append(f"""<tr>
              <td><span style="font-size:1.1rem;font-weight:700;color:{_grade_color(grade)}">{score:.0f}</span>
                  <span style="font-size:.75rem;color:{_grade_color(grade)};margin-left:3px">{grade}</span></td>
              <td>{badge}</td>
              <td style="color:var(--text2)">{c.get('provider','?')}</td>
              <td style="color:var(--text2);font-size:.82rem">{c.get('model','?')}</td>
              <td style="color:var(--text2)">{c.get('suite','?')}</td>
              <td style="color:var(--green)">+{cost_imp:.1f}%</td>
              <td style="color:var(--blue)">+{tok_imp:.1f}%</td>
              <td style="color:var(--text2);font-size:.8rem">{ts}</td>
              <td>{view_link}</td>
            </tr>""")
        return "".join(rows)

    best = max(certs, key=lambda c: c.get("overall", 0)) if certs else None
    best_score = f"{best['overall']:.0f}" if best else "—"
    best_grade = best.get("grade", "—") if best else "—"
    best_grade_color = _grade_color(best_grade) if best else "var(--text2)"
    n_certified = sum(1 for c in certs if c.get("certified"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore — Certification</title>
<style>{_DESIGN_CSS}</style>
</head>
<body>
<nav class="nav">
  <div class="nav-logo">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="url(#lg)" stroke-width="2.5"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#6488f5"/><stop offset="100%" stop-color="#8aaaf8"/></linearGradient></defs><polygon points="13,2 3,14 12,14 11,22 21,10 12,10"/></svg>
    RunCore
  </div>
  <div class="nav-links">
    <a href="/" class="nav-link">Dashboard</a>
    <a href="/certification" class="nav-link active">Certification</a>
    <a href="/leaderboard" class="nav-link">Leaderboard</a>
    <a href="/cloud/dashboard" class="nav-link">Cloud</a>
    <a href="/cloud/billing/plans" class="nav-link">Pricing</a>
  </div>
  <div class="nav-right">
    <button class="nav-icon-btn" onclick="document.getElementById('help-cert-modal').classList.add('open')" title="About RunCore Score">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09,9a3,3,0,0,1,5.83,1c0,2-3,3-3,3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
    </button>
  </div>
</nav>

<!-- Help modal -->
<div class="modal-overlay" id="help-cert-modal" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="modal-panel">
    <div class="modal-header">
      <span class="modal-title">About RunCore Score™</span>
      <button class="modal-close" onclick="document.getElementById('help-cert-modal').classList.remove('open')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="modal-body">
      <div class="help-section">
        <div class="help-heading">What is the RunCore Score?</div>
        <div class="help-text">A single 0–100 number that measures how much RunCore reduces your AI agent's cost and token usage versus an unguarded baseline. Score ≥ 60 = RunCore Certified.</div>
      </div>
      <div class="help-section">
        <div class="help-heading">Formula</div>
        <div class="help-text"><code>Score = 40% × Cost savings + 35% × Token reduction + 25% × Task success rate</code><br>Each dimension is scored 0–100 against its target. Hitting 25% cost savings scores 70 on that dimension; doubling the target scores 100.</div>
      </div>
      <div class="help-section">
        <div class="help-heading">Confidence interval</div>
        <div class="help-text">The 95% CI is computed from the standard deviation of per-run scores. More runs = tighter interval. Use ≥10 runs for production certification.</div>
      </div>
      <div class="help-section">
        <div class="help-heading">Fingerprint</div>
        <div class="help-text">Each report includes a SHA-256 fingerprint of the score data. Anyone can reproduce the result and verify the fingerprint matches.</div>
      </div>
    </div>
  </div>
</div>

<div class="page">

  <!-- KPI strip -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(91,138,247,0.1);color:var(--accent)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12,6 12,12 16,14"/></svg>
      </div>
      <div>
        <div class="kpi-label">Total Certifications</div>
        <div class="kpi-value" style="color:var(--accent)">{len(certs)}</div>
        <div class="kpi-sub">{n_certified} passed · {len(certs)-n_certified} not certified</div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(34,197,94,0.1);color:var(--green)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20,6 9,17 4,12"/></svg>
      </div>
      <div>
        <div class="kpi-label">Best Score</div>
        <div class="kpi-value" style="color:{best_grade_color}">{best_score}<span style="font-size:.85rem;color:var(--text2);margin-left:4px">{best_grade}</span></div>
        <div class="kpi-sub">{'provider: ' + best['provider'] if best else 'run a certification to see'}</div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(96,165,250,0.1);color:var(--blue)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9,9h6M9,12h6M9,15h4"/></svg>
      </div>
      <div>
        <div class="kpi-label">To reproduce</div>
        <div class="kpi-value" style="font-size:.85rem;color:var(--text);font-family:monospace">runcore certify</div>
        <div class="kpi-sub">any machine, same results</div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(251,191,36,0.1);color:var(--yellow)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12,2l3.09,6.26L22,9.27l-5,4.87 1.18,6.88L12,17.77l-6.18,3.25 1.18-6.88-5-4.87 6.91-1.01z"/></svg>
      </div>
      <div>
        <div class="kpi-label">Certified</div>
        <div class="kpi-value" style="color:var(--yellow)">{n_certified}/{len(certs) if certs else '—'}</div>
        <div class="kpi-sub">score ≥ 60 to pass</div>
      </div>
    </div>
  </div>

  <div class="main-grid">
    <!-- Run form -->
    <div class="card">
      <div class="card-title">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5,3 19,12 5,21"/></svg>
        Run Certification
      </div>
      <form id="certform">
        <div class="field">
          <label>Provider</label>
          <select id="cert-provider">
            <option value="groq">Groq — free tier (GROQ_API_KEY)</option>
            <option value="gemini">Gemini — free tier (GEMINI_API_KEY)</option>
            <option value="ollama">Ollama — local (no key needed)</option>
          </select>
          <div class="field-hint">Provider used for baseline and guarded LLM calls</div>
        </div>
        <div class="field">
          <label>Task suite</label>
          <select id="cert-suite">
            <option value="all">All suites (support + research + coding + analytics)</option>
            <option value="support">Support — customer service agent</option>
            <option value="research">Research — web research agent</option>
            <option value="coding">Coding — bug fix agent</option>
            <option value="analytics">Analytics — data analysis agent</option>
          </select>
          <div class="field-hint">Each suite tests different waste patterns: duplicate calls, loops, context bloat</div>
        </div>
        <div class="form-row">
          <div class="field">
            <label>Runs per task</label>
            <input type="number" id="cert-runs" value="5" min="1" max="50">
            <div class="field-hint">More runs = tighter CI</div>
          </div>
          <div class="field">
            <label>Model override</label>
            <input type="text" id="cert-model" placeholder="auto (default)">
            <div class="field-hint">Leave blank for provider default</div>
          </div>
        </div>
        <button class="btn-run" type="submit" id="cert-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5,3 19,12 5,21"/></svg>
          Run Certification
        </button>
        <div class="run-msg" id="cert-msg"></div>
        <div class="progress-wrap" id="cert-progress" style="display:none">
          <div class="progress-header">
            <span id="cert-phase">Initializing…</span>
            <span id="cert-pct">0%</span>
          </div>
          <div class="progress-track">
            <div class="progress-fill" id="cert-bar"></div>
          </div>
        </div>
      </form>
    </div>

    <!-- Score preview (latest) -->
    <div class="card" style="display:flex;flex-direction:column;gap:0">
      <div class="card-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12,2l3.09,6.26L22,9.27l-5,4.87 1.18,6.88L12,17.77l-6.18,3.25 1.18-6.88-5-4.87 6.91-1.01z"/></svg>
        Latest Score
      </div>
      {'<div id="latest-score-panel">' + _render_latest_score(best) + '</div>' if best else '<div style="color:var(--text2);font-size:.88rem;padding:20px 0">No certifications yet. Run one to see your score.</div>'}
    </div>
  </div>

  <!-- History table -->
  <div class="card">
    <div class="card-title" style="margin-bottom:0">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12,6 12,12 16,14"/></svg>
      Certification History
    </div>
    <table style="margin-top:16px">
      <thead><tr>
        <th>Score</th><th>Status</th><th>Provider</th><th>Model</th><th>Suite</th>
        <th>Cost savings</th><th>Token reduction</th><th>Date</th><th>Report</th>
      </tr></thead>
      <tbody id="cert-table">{_cert_rows()}</tbody>
    </table>
  </div>

</div>

<script>
document.getElementById('certform').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const btn = document.getElementById('cert-btn');
  const msg = document.getElementById('cert-msg');
  const progress = document.getElementById('cert-progress');
  const bar = document.getElementById('cert-bar');
  const phase = document.getElementById('cert-phase');
  const pct = document.getElementById('cert-pct');

  btn.disabled = true;
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12,6 12,12 16,14"/></svg> Running…';
  msg.textContent = '';
  msg.className = 'run-msg';
  progress.style.display = 'block';
  bar.style.width = '5%';
  phase.textContent = 'Starting certification…';
  pct.textContent = '5%';

  try {{
    const body = {{
      provider: document.getElementById('cert-provider').value,
      suite: document.getElementById('cert-suite').value,
      runs_per_task: parseInt(document.getElementById('cert-runs').value),
      model: document.getElementById('cert-model').value || null,
    }};

    const resp = await fetch('/certification/run', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body),
    }});

    if (!resp.ok) {{
      const err = await resp.json();
      throw new Error(err.detail || 'Certification failed');
    }}

    const result = await resp.json();
    const score = result.overall;
    const grade = result.grade;
    const certified = result.certified;

    bar.style.width = '100%';
    phase.textContent = `Score: ${{score}}/100 (${{grade}}) — ${{certified ? '✅ Certified' : '❌ Not certified'}}`;
    pct.textContent = '100%';

    msg.textContent = `RunCore Score: ${{score}}/100 (${{grade}})`;
    msg.className = 'run-msg' + (certified ? ' ok' : '');

    if (result.report_url) {{
      msg.innerHTML += ` — <a href="${{result.report_url}}" style="color:var(--accent)" target="_blank">view report</a>`;
    }}

    setTimeout(() => location.reload(), 2000);
  }} catch(err) {{
    msg.textContent = '✗ ' + err.message;
    msg.className = 'run-msg err';
    progress.style.display = 'none';
  }} finally {{
    btn.disabled = false;
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5,3 19,12 5,21"/></svg> Run Certification';
  }}
}});
</script>
</body>
</html>"""


def _render_latest_score(cert: dict | None) -> str:
    if not cert:
        return ""
    grade = cert.get("grade", "?")
    score = cert.get("overall", 0)
    certified = cert.get("certified", False)
    dims = cert.get("dimensions", [])
    grade_color = {"A+": "#22c55e", "A": "#22c55e", "B+": "#60a5fa", "B": "#6488f5",
                   "C": "#fbbf24", "F": "#ef4444"}.get(grade, "#94a3b8")
    ci = cert.get("confidence_interval_95", [score, score])
    html_file = cert.get("html_file", "")

    dim_bars = ""
    for d in dims:
        imp = d.get("improvement_pct", 0)
        sc = min(100, max(0, d.get("score", 0)))
        passed = d.get("passed", False)
        color = "#22c55e" if passed else "#f59e0b"
        dim_bars += f"""<div style="margin-bottom:14px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="font-size:.82rem;color:var(--text2)">{d.get('name','')}</span>
            <span style="font-size:.82rem;font-weight:600;color:{color}">+{imp:.1f}%</span>
          </div>
          <div style="background:var(--surface2);border-radius:4px;height:6px">
            <div style="width:{sc}%;height:100%;background:{color};border-radius:4px"></div>
          </div>
        </div>"""

    report_link = f'<a href="/certification/reports/{html_file}" style="color:var(--accent);font-size:.82rem;text-decoration:none" target="_blank">↗ full report</a>' if html_file else ""

    return f"""
    <div style="text-align:center;padding:20px 0 16px">
      <div style="font-size:3.5rem;font-weight:800;line-height:1;color:{grade_color}">{score:.0f}</div>
      <div style="font-size:1rem;font-weight:600;color:{grade_color};margin-top:2px">{grade}</div>
      <div style="margin-top:8px">
        <span style="font-size:.82rem;padding:4px 14px;border-radius:100px;{'background:rgba(34,197,94,.12);color:#22c55e;border:1px solid rgba(34,197,94,.25)' if certified else 'background:rgba(248,113,113,.12);color:#f87171;border:1px solid rgba(248,113,113,.25)'}">
          {'✓ RunCore Certified' if certified else '✗ Not Certified'}
        </span>
      </div>
      <div style="font-size:.75rem;color:var(--muted);margin-top:6px">CI [{ci[0]:.1f} — {ci[1]:.1f}] · {cert.get('provider','?')} / {cert.get('model','?')}</div>
    </div>
    <div style="border-top:1px solid var(--border);padding-top:16px;margin-top:4px">
      {dim_bars}
    </div>
    <div style="text-align:right;margin-top:4px">{report_link}</div>"""


def _cpst_from_cert(cert: dict) -> float | None:
    """Derive a CpST proxy (optimized cost per task) from a cert's Cost dimension."""
    for d in cert.get("dimensions", []):
        if "Cost" in d.get("name", ""):
            return d.get("optimized")
    return None


@app.get("/start", response_class=HTMLResponse)
def start_page() -> str:
    """Onboarding page for new users — 3 steps to first certification."""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RunCore — Get Started</title>
<style>{_DESIGN_CSS}
.steps {{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px;margin:40px 0}}
.step {{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:32px;position:relative}}
.step-num {{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:var(--accent);color:#fff;font-weight:700;border-radius:50%;margin-bottom:16px;font-size:1rem}}
.step h3 {{font-size:1.1rem;font-weight:700;margin:0 0 10px;color:var(--text)}}
.step p {{color:var(--text2);font-size:.9rem;line-height:1.6;margin:0 0 16px}}
.code-block {{background:#0d1117;border:1px solid var(--border);border-radius:8px;padding:14px 16px;font-family:monospace;font-size:.82rem;color:#e6edf3;overflow-x:auto;margin:8px 0}}
.hero {{text-align:center;padding:60px 0 40px}}
.hero h1 {{font-size:2.2rem;font-weight:800;margin:0 0 12px}}
.hero p {{color:var(--text2);font-size:1.05rem;max-width:560px;margin:0 auto 32px}}
.providers {{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}}
.provider-chip {{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 14px;font-size:.8rem;color:var(--text2)}}
.cta {{display:inline-block;background:linear-gradient(135deg,#5577f3,#4a6cf5);color:#fff;text-decoration:none;font-weight:600;padding:12px 28px;border-radius:10px;font-size:.95rem;margin-top:8px}}
</style></head><body>
<nav class="nav">
  <div class="nav-brand">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
    <span>RunCore</span>
  </div>
  <div class="nav-links">
    <a href="/" class="nav-link">Dashboard</a>
    <a href="/certification" class="nav-link">Certification</a>
    <a href="/leaderboard" class="nav-link">Leaderboard</a>
    <a href="/pricing" class="nav-link">Pricing</a>
  </div>
</nav>
<main class="container" style="max-width:960px">
  <div class="hero">
    <h1>Stop your AI agent burning money.</h1>
    <p>RunCore wraps any agent, cuts the waste — duplicate tool calls, bloated context, runaway loops — and <strong>proves task success didn't drop</strong>. Gate it in CI before you ship.</p>
    <a href="/leaderboard" class="cta">See the leaderboard →</a>
  </div>

  <div class="steps">
    <div class="step">
      <div class="step-num">1</div>
      <h3>Wrap your agent</h3>
      <p>Any provider (OpenAI, Anthropic, Groq, local), any framework. One line turns on the runtime guards — no rewrite.</p>
      <div class="code-block">pip install runcore</div>
      <div class="code-block">with runcore.capture("agent",<br>&nbsp;&nbsp;guards=runcore.GuardConfig()):<br>&nbsp;&nbsp;&nbsp;&nbsp;my_agent.run(task)</div>
    </div>

    <div class="step">
      <div class="step-num">2</div>
      <h3>See the savings</h3>
      <p>RunCore reports exactly what it cut — tokens, cost — with a check that success held.</p>
      <div class="code-block">run.savings.summary_line()<br># saved 27% tokens, $0.0041/run,<br># success preserved</div>
      <p style="margin-top:12px">Measured on real runs: <strong>14–19% fewer tokens</strong>, success preserved.</p>
    </div>

    <div class="step">
      <div class="step-num">3</div>
      <h3>Gate it in CI</h3>
      <p>Fail the build when the agent regresses — more expensive or less reliable — before it reaches production.</p>
      <div class="code-block">runcore ci --update-baseline   # once<br>runcore ci                     # every PR</div>
      <a href="https://github.com/ptpaulinho/RunCore/blob/main/docs/CI_GATE.md" style="color:var(--accent);text-decoration:none;font-size:.85rem;font-weight:600">CI gate guide →</a>
    </div>
  </div>

  <div class="card" style="margin:40px 0;padding:32px">
    <h2 style="margin:0 0 8px;font-size:1.3rem">What you get</h2>
    <p style="color:var(--text2);margin:0 0 24px">A runtime that saves money automatically — plus the proof to show customers.</p>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px">
      <div style="background:var(--surface);border-radius:10px;padding:18px">
        <div style="font-size:1.5rem;margin-bottom:6px">✂️</div>
        <div style="font-weight:600;margin-bottom:4px">Automatic savings</div>
        <div style="color:var(--text2);font-size:.85rem">Guards cut duplicate calls, context bloat & loops at runtime</div>
      </div>
      <div style="background:var(--surface);border-radius:10px;padding:18px">
        <div style="font-size:1.5rem;margin-bottom:6px">🛡️</div>
        <div style="font-weight:600;margin-bottom:4px">No-regression proof</div>
        <div style="color:var(--text2);font-size:.85rem">Every cut is checked against task success — no silent breakage</div>
      </div>
      <div style="background:var(--surface);border-radius:10px;padding:18px">
        <div style="font-size:1.5rem;margin-bottom:6px">⚙️</div>
        <div style="font-weight:600;margin-bottom:4px">CI gate</div>
        <div style="color:var(--text2);font-size:.85rem">GitHub Action fails the build on cost/quality regression</div>
      </div>
      <div style="background:var(--surface);border-radius:10px;padding:18px">
        <div style="font-size:1.5rem;margin-bottom:6px">📊</div>
        <div style="font-weight:600;margin-bottom:4px">RunCore Score™ + badge</div>
        <div style="color:var(--text2);font-size:.85rem">Reproducible 0–100 proof to show customers — optional</div>
      </div>
    </div>
  </div>

  <div class="card" style="margin:0 0 60px;padding:32px;border:1px solid var(--accent)44">
    <h2 style="margin:0 0 8px;font-size:1.3rem">Submit to the leaderboard</h2>
    <p style="color:var(--text2);margin:0 0 20px">After certification, your SHA-256 fingerprinted report can be submitted publicly. Email your report JSON to <strong>ppereira@saber3d.pt</strong> or open a PR to the RunCore repo.</p>
    <a href="https://github.com/ptpaulinho/RunCore" style="color:var(--accent);text-decoration:none;font-weight:600">GitHub: ptpaulinho/RunCore →</a>
  </div>
</main>
</body></html>"""


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard_page() -> str:
    """Public efficiency leaderboard — agents ranked by RunCore Score™."""
    certs = _load_cert_history()
    # Keep only best result per (provider, model) pair
    best: dict[str, dict] = {}
    for c in certs:
        key = f"{c.get('provider','')}/{c.get('model','')}"
        if key not in best or c.get("overall", 0) > best[key].get("overall", 0):
            best[key] = c
    ranked = sorted(best.values(), key=lambda c: c.get("overall", 0), reverse=True)

    def _grade_color(grade: str) -> str:
        return {"A+": "#22c55e", "A": "#22c55e", "B+": "#60a5fa", "B": "#6488f5",
                "C": "#fbbf24", "F": "#ef4444"}.get(grade, "#94a3b8")

    if not ranked:
        rows = """<tr><td colspan="7" style="text-align:center;padding:48px;color:var(--text2)">
          No certified agents yet. Run a certification to claim the top spot.
          <div style="margin-top:14px"><a href="/certification" style="background:linear-gradient(135deg,#5577f3,#4a6cf5);color:#fff;text-decoration:none;font-size:.85rem;font-weight:500;padding:9px 18px;border-radius:8px">Run Certification →</a></div>
        </td></tr>"""
    else:
        rows = ""
        for i, c in enumerate(ranked, 1):
            grade = c.get("grade", "?")
            col = _grade_color(grade)
            cpst = _cpst_from_cert(c)
            cpst_fmt = f"${cpst:.6f}" if isinstance(cpst, (int, float)) else "—"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}")
            html_file = c.get("html_file", "")
            report = (f'<a href="/certification/reports/{html_file}" style="color:var(--accent);text-decoration:none;font-size:.8rem">report →</a>'
                      if html_file else "—")
            cert_chip = ('<span style="color:#22c55e;font-size:.75rem;font-weight:600">✓ Certified</span>'
                         if c.get("certified") else '<span style="color:#94a3b8;font-size:.75rem">not certified</span>')
            rows += f"""<tr>
              <td style="text-align:center;font-size:1rem;width:48px">{medal}</td>
              <td><div style="font-weight:600;color:var(--text)">{c.get('provider','?')}</div>
                  <div style="font-size:.76rem;color:var(--muted)">{c.get('model','?')}</div></td>
              <td><span style="display:inline-block;min-width:34px;text-align:center;background:{col};color:#06101f;font-weight:700;font-size:.85rem;padding:3px 8px;border-radius:6px">{grade}</span></td>
              <td style="font-weight:700;color:var(--text)">{c.get('overall',0):.1f}</td>
              <td style="font-size:.82rem;color:var(--text2)">{c.get('suite','?')}</td>
              <td style="font-size:.82rem;color:var(--text2);font-family:monospace">{cpst_fmt}</td>
              <td>{cert_chip} · {report}</td>
            </tr>"""

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RunCore — Efficiency Leaderboard</title>
<style>{_DESIGN_CSS}</style>
</head><body>
<nav class="nav">
  <div class="nav-brand">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
    <span>RunCore</span>
  </div>
  <div class="nav-links">
    <a href="/" class="nav-link">Dashboard</a>
    <a href="/certification" class="nav-link">Certification</a>
    <a href="/leaderboard" class="nav-link active">Leaderboard</a>
    <a href="/cloud/dashboard" class="nav-link">Cloud</a>
    <a href="/cloud/billing/plans" class="nav-link">Pricing</a>
  </div>
</nav>
<div class="page">
  <div style="margin-bottom:8px">
    <h1 style="font-size:1.6rem;font-weight:700;color:var(--text);margin:0">Efficiency Leaderboard</h1>
    <p style="color:var(--text2);font-size:.92rem;margin:8px 0 0;max-width:640px">
      AI agents ranked by <b>RunCore Score™</b> — the open standard for cost &amp; token efficiency.
      Higher score = more successful work per dollar. <a href="https://github.com/ptpaulinho/RunCore/blob/main/docs/RUNCORE_SCORE_SPEC.md" style="color:var(--accent)">How it's scored →</a>
    </p>
  </div>
  <div class="card" style="margin-top:20px">
    <table style="width:100%">
      <thead><tr>
        <th style="text-align:center">#</th><th>Agent</th><th>Grade</th><th>Score</th>
        <th>Suite</th><th>CpST</th><th>Status</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <p style="color:var(--muted);font-size:.78rem;margin-top:14px">
    CpST = Cost per Successful Task (optimized). Results are reproducible and SHA-256 fingerprinted.
  </p>
  <div class="card" style="margin-top:24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px">
    <div>
      <div style="font-weight:600;color:var(--text);font-size:1rem">Get your agent listed</div>
      <div style="color:var(--text2);font-size:.85rem;margin-top:4px">Run a certification — your score, badge, and report appear here automatically.</div>
    </div>
    <code style="background:var(--surface2);color:var(--accent);padding:10px 14px;border-radius:8px;font-size:.85rem">runcore certify --provider groq</code>
  </div>
</div>
</body></html>"""


@app.post("/certification/run")
async def run_certification_endpoint(request: Request) -> dict:
    """Run the certification suite in the background and return when done."""
    import asyncio
    body = await request.json()
    provider = body.get("provider", "groq")
    suite = body.get("suite", "all")
    runs_per_task = int(body.get("runs_per_task", 5))
    model = body.get("model") or None

    def _run():
        from benchmarks.certification import run_certification, save_cert
        score = run_certification(
            provider_name=provider,
            model=model,
            runs_per_task=runs_per_task,
            suite=suite,
            verbose=False,
        )
        out = save_cert(score)
        return score, out

    loop = asyncio.get_event_loop()
    try:
        score, out = await loop.run_in_executor(None, _run)
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "overall": score.overall,
        "grade": score.grade,
        "certified": score.certified,
        "provider": score.provider,
        "model": score.model,
        "n_runs": score.n_runs,
        "confidence_interval_95": list(score.confidence_interval_95),
        "report_url": f"/certification/reports/{out.name}",
        "dimensions": [
            {"name": d.name, "score": d.score, "improvement_pct": d.improvement_pct, "passed": d.passed}
            for d in score.dimensions
        ],
    }


_GRADE_COLORS = {
    "A+": "#22c55e", "A": "#22c55e",
    "B+": "#3b82f6", "B": "#3b82f6",
    "C": "#f59e0b", "F": "#ef4444",
}


def _badge_svg(label: str, value: str, color: str) -> str:
    """Render a shields.io-style SVG badge (self-contained, no external deps)."""
    # Rough width estimation: 6.5px per char + padding.
    lw = int(len(label) * 6.5) + 22
    vw = int(len(value) * 6.5) + 22
    total = lw + vw
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{label}: {value}">
<title>{label}: {value}</title>
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>
<g clip-path="url(#r)">
<rect width="{lw}" height="20" fill="#1b2333"/>
<rect x="{lw}" width="{vw}" height="20" fill="{color}"/>
<rect width="{total}" height="20" fill="url(#s)"/>
</g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
<text x="{lw/2:.0f}" y="14" fill="#000" fill-opacity=".3">{label}</text>
<text x="{lw/2:.0f}" y="13">{label}</text>
<text x="{lw+vw/2:.0f}" y="14" fill="#000" fill-opacity=".3" font-weight="bold">{value}</text>
<text x="{lw+vw/2:.0f}" y="13" font-weight="bold">{value}</text>
</g>
</svg>'''


@app.get("/badge/{grade}.svg")
def badge_grade(grade: str) -> Response:
    """Embeddable 'RunCore Certified — Grade X' badge. Use in READMEs / landing pages."""
    grade = grade.upper().replace("PLUS", "+")
    color = _GRADE_COLORS.get(grade, "#6b7280")
    svg = _badge_svg("RunCore Certified", grade, color)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "max-age=300"})


@app.get("/badge/score/{value}.svg")
def badge_score(value: float) -> Response:
    """Dynamic score badge, e.g. /badge/score/84.svg -> 'RunCore Score: 84'."""
    if value >= 80:
        color = "#22c55e"
    elif value >= 60:
        color = "#3b82f6"
    elif value >= 40:
        color = "#f59e0b"
    else:
        color = "#ef4444"
    svg = _badge_svg("RunCore Score", f"{value:.0f}/100", color)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "max-age=300"})


@app.get("/certification/reports/{filename}", response_class=HTMLResponse)
def serve_cert_report(filename: str) -> str:
    """Serve a saved certification HTML report."""
    from fastapi import HTTPException
    from benchmarks.certification import RESULTS_DIR
    cert_dir = RESULTS_DIR / "certifications"
    path = cert_dir / filename
    if not path.exists() or not path.suffix == ".html":
        raise HTTPException(status_code=404, detail="Report not found")
    return path.read_text(encoding="utf-8")


# ===========================================================================
# Settings — provider keys, status, and in-dashboard test runner
# ===========================================================================

@app.get("/settings/status")
def settings_status() -> dict:
    """Provider availability + masked keys for the Settings panel."""
    return {"providers": _config.provider_status()}


class SaveKeysRequest(BaseModel):
    keys: dict[str, str]


@app.post("/settings/keys")
def save_keys(req: SaveKeysRequest) -> dict:
    """Persist provider API keys (applied to the running server immediately)."""
    _config.set_keys(req.keys)
    return {"ok": True, "providers": _config.provider_status()}


@app.post("/tests/run")
async def run_tests_endpoint(request: Request) -> dict:
    """Run the offline unit-test suite and return a structured summary — no terminal needed."""
    import subprocess
    import sys
    import re

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    target = body.get("target", "tests/unit/")
    # Guard against arbitrary paths — only allow the known test dirs.
    if target not in ("tests/unit/", "tests/integration/", "tests/"):
        target = "tests/unit/"

    def _run():
        cmd = [sys.executable, "-m", "pytest", target, "-q", "--no-header", "-p", "no:cacheprovider"]
        # test_server.py spins up its own TestClient and would deadlock inside the live server.
        if target in ("tests/unit/", "tests/"):
            cmd += ["--ignore=tests/unit/test_server.py"]
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(Path.cwd()),
        )

    loop = asyncio.get_event_loop()
    try:
        proc = await loop.run_in_executor(None, _run)
    except subprocess.TimeoutExpired:
        return {"ok": False, "summary": "Timed out after 300s", "passed": 0, "failed": 0, "output": ""}
    except Exception as exc:
        return {"ok": False, "summary": f"Failed to run: {exc}", "passed": 0, "failed": 0, "output": ""}

    out = (proc.stdout or "") + (proc.stderr or "")
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)  # strip ANSI colour codes
    passed = failed = 0
    m = re.search(r"(\d+)\s+passed", out)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", out)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+)\s+error", out)
    errors = int(m.group(1)) if m else 0

    # Last meaningful line as summary
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    summary = lines[-1] if lines else "No output"
    return {
        "ok": proc.returncode == 0,
        "passed": passed,
        "failed": failed + errors,
        "summary": summary,
        "output": out[-4000:],  # tail, for the UI
        "target": target,
    }


# ===========================================================================
# RunCore Cloud — Multi-tenant ingest API
# ===========================================================================

def _require_tenant(request: Request) -> dict:
    """Extract and validate Bearer API key from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <api_key>")
    api_key = auth.removeprefix("Bearer ").strip()
    tenant = _store.get_tenant_by_key(api_key)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return tenant


# ---------------------------------------------------------------------------
# Tenant management (admin — no auth for simplicity; add middleware in prod)
# ---------------------------------------------------------------------------

class CreateTenantRequest(BaseModel):
    name: str
    plan: str = "free"


def _require_admin(request: Request) -> None:
    """Enforce ADMIN_TOKEN header on tenant management endpoints."""
    import os
    admin_token = os.getenv("ADMIN_TOKEN")
    if admin_token:
        provided = request.headers.get("X-Admin-Token", "")
        if not provided or provided != admin_token:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Token")


@app.post("/cloud/tenants", status_code=201)
def create_tenant(req: CreateTenantRequest, request: Request) -> dict:
    """Create a new tenant. Requires X-Admin-Token header when ADMIN_TOKEN env var is set."""
    _require_admin(request)
    return _store.create_tenant(name=req.name, plan=req.plan)


@app.get("/cloud/tenants")
def list_tenants(request: Request) -> dict:
    """List all tenants (admin view — id, name, plan, created_at; no API keys)."""
    _require_admin(request)
    return {"tenants": _store.list_tenants()}


# ---------------------------------------------------------------------------
# Ingest — authenticated by API key
# ---------------------------------------------------------------------------

@app.post("/cloud/ingest")
async def ingest_traces(request: Request) -> dict:
    """Ingest one or more ATIR traces for the authenticated tenant.

    Body: ``{"traces": [<ATIRTrace dict>, ...]}``  (array always, even for one)

    Returns: ``{"ingested": N, "trace_ids": [...]}``

    Example::

        import runcore, requests

        with runcore.capture("my_agent", task="classify") as t:
            ...

        trace = t.get_atir()
        resp = requests.post(
            "https://your-runcore-cloud/cloud/ingest",
            headers={"Authorization": "Bearer rc_<key>"},
            json={"traces": [trace.model_dump()]},
        )
    """
    tenant = _require_tenant(request)
    body = await request.json()
    raw_traces = body.get("traces", [])
    if not raw_traces:
        raise HTTPException(status_code=400, detail="No traces provided")

    # --- Tier limit check ---
    usage = _store.get_monthly_usage(tenant["id"])
    allowed, reason = _billing.check_ingest_allowed(tenant["plan"], usage, len(raw_traces))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={"error": "trace_limit_exceeded", "message": reason, "plan": tenant["plan"]},
        )

    ids = []
    errors = []
    for i, t in enumerate(raw_traces):
        try:
            tid = _store.ingest_trace(tenant["id"], t)
            ids.append(tid)
        except Exception as exc:
            errors.append({"index": i, "error": str(exc)})

    limits = _billing.get_limits(tenant["plan"])
    return {
        "ingested": len(ids),
        "trace_ids": ids,
        "errors": errors,
        "tenant_id": tenant["id"],
        "usage": {
            "traces_this_month": usage + len(ids),
            "limit": limits.traces_per_month,
            "plan": tenant["plan"],
        },
    }


# ---------------------------------------------------------------------------
# Tenant trace listing + detail
# ---------------------------------------------------------------------------

@app.get("/cloud/traces")
def get_traces(request: Request, limit: int = 50, offset: int = 0) -> dict:
    """List traces for the authenticated tenant, newest first."""
    tenant = _require_tenant(request)
    traces = _store.list_traces(tenant["id"], limit=limit, offset=offset)
    return {"traces": traces, "tenant_id": tenant["id"], "count": len(traces)}


@app.get("/cloud/traces/{trace_id}")
def get_trace(trace_id: str, request: Request) -> dict:
    """Return the full ATIR trace JSON for a single trace."""
    tenant = _require_tenant(request)
    trace = _store.get_trace(tenant["id"], trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return trace


# ---------------------------------------------------------------------------
# Tenant dashboard — HTML + JSON stats
# ---------------------------------------------------------------------------

@app.get("/cloud/dashboard", response_class=HTMLResponse)
def tenant_dashboard(request: Request) -> str:
    """HTML dashboard scoped to the authenticated tenant's traces."""
    tenant = _require_tenant(request)
    stats  = _store.tenant_stats(tenant["id"])
    traces = _store.list_traces(tenant["id"], limit=20)

    total      = stats.get("total_traces") or 0
    avg_cost   = stats.get("avg_cost") or 0
    avg_cpst   = stats.get("avg_cpst") or 0
    best_cpst  = stats.get("best_cpst") or 0
    succ_rate  = stats.get("success_rate") or 0
    total_cost = stats.get("total_cost") or 0
    last_trace = (stats.get("last_trace") or "—")[:10]
    agents     = stats.get("agents") or 0

    rows = ""
    for tr in traces:
        sid   = (tr.get("id") or "")[:8]
        agent = tr.get("agent_name") or "—"
        fw    = tr.get("framework") or "—"
        task  = (tr.get("task") or "—")[:50]
        st    = (tr.get("started_at") or "—")[:19].replace("T", " ")
        ok    = "✓" if tr.get("success") else "✗"
        color = "#4ade80" if tr.get("success") else "#f87171"
        cost  = f'${tr["total_cost_usd"]:.5f}' if tr.get("total_cost_usd") else "—"
        cpst  = f'${tr["cpst"]:.5f}' if tr.get("cpst") else "—"
        tok   = str(tr.get("total_tokens") or "—")
        rows += f"""<tr>
          <td style="font-family:monospace;font-size:.78rem;color:#94a3b8">{sid}</td>
          <td>{agent}</td><td style="color:#94a3b8">{fw}</td>
          <td style="color:#94a3b8;font-size:.8rem">{task}</td>
          <td style="color:{color};font-weight:600">{ok}</td>
          <td style="font-family:monospace">{cost}</td>
          <td style="font-family:monospace;color:#8aaaf8">{cpst}</td>
          <td style="color:#60a5fa">{tok}</td>
          <td style="color:#64748b;font-size:.8rem">{st}</td>
        </tr>"""

    _succ_color = "var(--green)" if succ_rate >= 80 else "var(--red)"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore Cloud — {tenant['name']}</title>
<style>{_DESIGN_CSS}
.tenant-badge {{
  display: inline-flex; align-items: center;
  background: rgba(91,138,247,0.1);
  border: 1px solid var(--border-m);
  color: var(--accent);
  padding: 4px 12px;
  border-radius: 20px;
  font-size: .78rem;
  font-weight: 700;
}}
.plan-badge {{
  display: inline-flex; align-items: center;
  background: rgba(96,165,250,0.1);
  border: 1px solid rgba(96,165,250,0.2);
  color: var(--blue);
  padding: 4px 12px;
  border-radius: 20px;
  font-size: .75rem;
  font-weight: 600;
}}
</style>
</head>
<body>
<nav class="nav">
  <div class="nav-logo">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="url(#lg2)" stroke-width="2.5"><defs><linearGradient id="lg2" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#6488f5"/><stop offset="100%" stop-color="#8aaaf8"/></linearGradient></defs><polygon points="13,2 3,14 12,14 11,22 21,10 12,10"/></svg>
    RunCore
  </div>
  <div class="nav-links">
    <a href="/" class="nav-link">Dashboard</a>
    <a href="/certification" class="nav-link">Certification</a>
    <a href="/leaderboard" class="nav-link">Leaderboard</a>
    <a href="/cloud/dashboard" class="nav-link active">Cloud</a>
    <a href="/cloud/billing/plans" class="nav-link">Pricing</a>
  </div>
  <span class="tenant-badge" style="margin-left:16px">{tenant['name']}</span>
  <span class="plan-badge" style="margin-left:8px">{tenant['plan']}</span>
  <div class="nav-right">
    <span>tenant: {tenant['id'][:8]}…</span>
    <span style="color:var(--border-m)">·</span>
    <span>last trace: {last_trace}</span>
  </div>
</nav>
<div class="page">
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(91,138,247,0.1);color:var(--accent)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
      </div>
      <div>
        <div class="kpi-label">Total Traces</div>
        <div class="kpi-value" style="color:var(--accent)">{total}</div>
        <div class="kpi-sub">{agents} agent(s)</div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(52,211,153,0.1);color:var(--green)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22,4 12,14.01 9,11.01"/></svg>
      </div>
      <div>
        <div class="kpi-label">Success Rate</div>
        <div class="kpi-value" style="color:{_succ_color}">{succ_rate:.1f}%</div>
        <div class="kpi-sub">of all traces</div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(96,165,250,0.1);color:var(--blue)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
      </div>
      <div>
        <div class="kpi-label">Avg CpST</div>
        <div class="kpi-value" style="color:var(--blue)">${avg_cpst:.5f}</div>
        <div class="kpi-sub">cost per successful task</div>
      </div>
    </div>
    <div class="kpi-card">
      <div class="kpi-icon" style="background:rgba(138,170,248,0.1);color:var(--accent-2)">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/></svg>
      </div>
      <div>
        <div class="kpi-label">Total Spend</div>
        <div class="kpi-value" style="color:var(--accent-2)">${total_cost:.4f}</div>
        <div class="kpi-sub">avg ${avg_cost:.5f}/trace</div>
      </div>
    </div>
  </div>

  <div class="card" style="margin-bottom:20px">
    <div class="card-title">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12,6 12,12 16,14"/></svg>
      Recent Traces (last 20)
    </div>
    {'<table><thead><tr><th>ID</th><th>Agent</th><th>Framework</th><th>Task</th><th>OK</th><th>Cost</th><th>CpST</th><th>Tokens</th><th>Started</th></tr></thead><tbody>' + rows + '</tbody></table>' if traces else '<div class="empty-state"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg><p>No traces ingested yet. Use the API to push your first trace.</p></div>'}
  </div>

  <div class="card">
    <div class="card-title">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16,18 22,12 16,6"/><polyline points="8,6 2,12 8,18"/></svg>
      Ingest API — Quick Start
    </div>
    <pre class="code-block"><span class="kw">import</span> runcore, requests

<span class="kw">with</span> runcore.capture(<span class="st">"my_agent"</span>, task=<span class="st">"classify"</span>) <span class="kw">as</span> tracer:
    ...  <span class="cm"># your agent code</span>

trace = tracer.get_atir()
requests.post(
    <span class="st">"https://your-runcore-cloud/cloud/ingest"</span>,
    headers={{"Authorization": <span class="st">"Bearer rc_YOUR_API_KEY"</span>}},
    json={{"traces": [trace.model_dump()]}},
)</pre>
  </div>
</div>
</body>
</html>"""


@app.get("/cloud/stats")
def tenant_stats_api(request: Request) -> dict:
    """Return KPI stats for the authenticated tenant as JSON."""
    tenant = _require_tenant(request)
    stats  = _store.tenant_stats(tenant["id"])
    usage  = _store.get_monthly_usage(tenant["id"])
    limits = _billing.get_limits(tenant["plan"])
    return {
        "tenant_id": tenant["id"],
        "tenant_name": tenant["name"],
        **stats,
        "plan": tenant["plan"],
        "traces_this_month": usage,
        "traces_limit": limits.traces_per_month,
    }


# ===========================================================================
# Billing endpoints
# ===========================================================================

class CheckoutRequest(BaseModel):
    plan: str
    email: str = ""


@app.post("/cloud/billing/checkout")
async def billing_checkout(req: CheckoutRequest, request: Request) -> dict:
    """Create a Stripe Checkout Session for upgrading the authenticated tenant's plan.

    Returns ``{"url": "<checkout_url>", "session_id": "...", "dev_mode": bool}``
    """
    tenant = _require_tenant(request)
    if req.plan not in ("team", "enterprise"):
        raise HTTPException(status_code=400, detail="Invalid plan. Choose 'team' or 'enterprise'.")
    result = _stripe.create_checkout_session(
        tenant_id=tenant["id"],
        plan=req.plan,
        email=req.email,
    )
    return result


@app.post("/cloud/billing/portal")
async def billing_portal(request: Request) -> dict:
    """Return a Stripe Customer Portal URL for managing subscription."""
    tenant = _require_tenant(request)
    full = _store.get_tenant_by_id(tenant["id"])
    customer_id = (full or {}).get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(
            status_code=400,
            detail="No Stripe customer found. Subscribe first via /cloud/billing/checkout.",
        )
    url = _stripe.create_portal_session(customer_id)
    return {"url": url}


@app.post("/cloud/billing/webhook")
async def billing_webhook(request: Request) -> dict:
    """Stripe webhook receiver. Register this URL in the Stripe Dashboard."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    event = _stripe.verify_webhook(payload, sig)
    if event is None:
        raise HTTPException(status_code=400, detail="Webhook signature verification failed")
    action = _stripe.handle_webhook_event(event, _store)
    return {"received": True, "action": action}


@app.get("/cloud/billing/plans", response_class=HTMLResponse)
def billing_plans(request: Request) -> str:
    """Pricing page with plan comparison table."""
    plans = _billing.TIER_COMPARISON

    def _plan_card(p: dict) -> str:
        is_team = p["plan"] == "team"
        is_free = p["plan"] == "free"
        glow_style = (
            "border-color:rgba(91,138,247,0.4);box-shadow:0 0 0 1px rgba(91,138,247,0.2),0 8px 40px rgba(85,119,243,0.15);"
            if is_team else ""
        )
        badge = (
            '<div style="position:absolute;top:-13px;left:50%;transform:translateX(-50%);'
            'background:linear-gradient(135deg,#7c3aed,#6366f1);color:#fff;font-size:.72rem;'
            'font-weight:800;padding:4px 14px;border-radius:20px;white-space:nowrap;'
            'letter-spacing:.5px;box-shadow:0 2px 12px rgba(85,119,243,0.4)">Most Popular</div>'
            if is_team else ""
        )
        feats = "".join(
            f'<li style="display:flex;align-items:center;gap:8px;font-size:.85rem;color:#94a3b8;'
            f'padding:6px 0;border-bottom:1px solid rgba(91,138,247,0.07)">'
            f'<span style="color:#34d399;font-weight:700;flex-shrink:0">✓</span>{f}</li>'
            for f in p["features"]
        )
        if is_free:
            cta = ('<span style="display:block;margin-top:20px;padding:10px;background:var(--surface2);'
                   'color:var(--muted);border:1px solid var(--border);border-radius:8px;'
                   'text-align:center;font-size:.88rem">Current plan</span>')
        else:
            cta = (f'<a href="/cloud/billing/checkout-page?plan={p["plan"]}" '
                   f'style="display:block;margin-top:20px;padding:11px;'
                   f'background:linear-gradient(135deg,#7c3aed,#6366f1);color:#fff;'
                   f'border-radius:8px;text-align:center;text-decoration:none;font-weight:700;'
                   f'font-size:.9rem;box-shadow:0 0 20px rgba(85,119,243,0.3);transition:all .2s">'
                   f'Upgrade to {p["plan"].title()}</a>')
        price_color = "color:var(--text)" if is_free else "background:linear-gradient(135deg,#6488f5,#8aaaf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text"
        return f"""
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:16px;
          padding:28px;position:relative;box-shadow:0 4px 20px rgba(0,0,0,0.3);
          transition:transform .2s,box-shadow .2s;{glow_style}">
          {badge}
          <div style="font-size:.75rem;font-weight:800;text-transform:uppercase;letter-spacing:1.5px;
            color:var(--muted);margin-bottom:10px">{p["plan"].title()}</div>
          <div style="font-size:2.4rem;font-weight:800;line-height:1;margin-bottom:4px;{price_color}">{p["price"]}</div>
          <div style="font-size:.8rem;color:var(--muted);margin-bottom:20px">{p["traces"]} traces / month</div>
          <ul style="list-style:none;margin-bottom:16px">{feats}</ul>
          <div style="font-size:.76rem;color:var(--muted);margin-bottom:3px">Data retained: {p["retention"]}</div>
          <div style="font-size:.76rem;color:var(--muted);margin-bottom:0">Seats: {p["seats"]}</div>
          {cta}
        </div>"""

    cards = "".join(_plan_card(p) for p in plans)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore Cloud — Pricing</title>
<style>{_DESIGN_CSS}
.hero-title {{
  font-size: 2.6rem;
  font-weight: 800;
  letter-spacing: -1.5px;
  line-height: 1.1;
  background: linear-gradient(135deg, #f1f5f9 30%, #6488f5);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 14px;
}}
.hero-sub {{ color: var(--text2); margin-bottom: 56px; font-size: 1.05rem; }}
</style>
</head>
<body>
<nav class="nav">
  <div class="nav-logo">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="url(#lg3)" stroke-width="2.5"><defs><linearGradient id="lg3" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#6488f5"/><stop offset="100%" stop-color="#8aaaf8"/></linearGradient></defs><polygon points="13,2 3,14 12,14 11,22 21,10 12,10"/></svg>
    RunCore
  </div>
  <div class="nav-links">
    <a href="/" class="nav-link">Dashboard</a>
    <a href="/certification" class="nav-link">Certification</a>
    <a href="/leaderboard" class="nav-link">Leaderboard</a>
    <a href="/cloud/dashboard" class="nav-link">Cloud</a>
    <a href="/cloud/billing/plans" class="nav-link active">Pricing</a>
  </div>
</nav>
<div class="page" style="text-align:center;padding-top:60px">
  <div class="hero-title">From self-certify to continuous certification</div>
  <p class="hero-sub">The SDK and self-certification are free forever. Pay when you need your agent's efficiency proven, tracked, and protected in production.</p>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px;text-align:left;max-width:960px;margin:0 auto">
    {cards}
  </div>
</div>
</body>
</html>"""


@app.get("/cloud/billing/checkout-page", response_class=HTMLResponse)
def checkout_page(plan: str = "team", tenant: str = "") -> HTMLResponse:
    """Redirect pricing-page upgrade buttons to the correct checkout flow."""
    from fastapi.responses import RedirectResponse
    import stripe as _stripe_mod
    import os
    if os.getenv("STRIPE_SECRET_KEY"):
        url = f"/cloud/billing/stripe-checkout?plan={plan}"
        if tenant:
            url += f"&tenant={tenant}"
        return RedirectResponse(url)
    return RedirectResponse(f"/cloud/billing/dev-checkout?plan={plan}&tenant={tenant}")


@app.get("/cloud/billing/dev-checkout", response_class=HTMLResponse)
def dev_checkout(plan: str = "team", tenant: str = "") -> str:
    """Dev-mode checkout page (shown when no Stripe keys are configured)."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dev Checkout — RunCore</title>
<style>{_DESIGN_CSS}</style>
</head>
<body style="display:flex;align-items:center;justify-content:center;min-height:100vh">
<div class="card" style="max-width:460px;width:100%;text-align:center;padding:40px 36px">
  <div style="width:52px;height:52px;border-radius:14px;background:rgba(251,191,36,0.1);
    border:1px solid rgba(251,191,36,0.2);display:flex;align-items:center;justify-content:center;
    margin:0 auto 20px;color:var(--yellow)">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
  </div>
  <div style="font-size:1.25rem;font-weight:800;color:var(--text);margin-bottom:10px;letter-spacing:-.5px">Dev Mode Checkout</div>
  <p style="color:var(--text2);margin-bottom:24px;font-size:.9rem;line-height:1.6">
    Stripe keys are not configured. In production, add <code style="background:var(--surface2);padding:2px 6px;border-radius:4px;font-size:.82rem">STRIPE_SECRET_KEY</code> to enable real payments.
  </p>
  <div style="display:inline-flex;align-items:center;gap:8px;background:rgba(251,191,36,0.08);
    border:1px solid rgba(251,191,36,0.2);color:#fcd34d;padding:6px 16px;border-radius:20px;
    font-size:.82rem;font-weight:600;margin-bottom:24px">
    Plan: {plan.title()} &nbsp;·&nbsp; Tenant: {tenant[:8] or "—"}
  </div>
  <div>
    <a href="/cloud/billing/plans" style="color:var(--accent);font-size:.88rem">← Back to plans</a>
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Auth routes — register, login, logout, session helper
# ---------------------------------------------------------------------------

from fastapi import Cookie, Form
from fastapi.responses import RedirectResponse


def _get_tenant(session: str | None = Cookie(default=None)) -> dict | None:
    if not session:
        return None
    return _store.get_session(session)


def _require_session_tenant(session: str | None = Cookie(default=None)) -> dict:
    """Cookie/session auth for dashboard routes (distinct from the Bearer-key
    _require_tenant used by /cloud/* API endpoints)."""
    tenant = _get_tenant(session)
    if not tenant:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return tenant


@app.get("/login", response_class=HTMLResponse)
def login_page(error: str = ""):
    err_html = f'<div style="color:#ef4444;margin-bottom:16px;font-size:.9rem">{error}</div>' if error else ""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore — Login</title>
<style>{_DESIGN_CSS}
.auth-card{{max-width:420px;margin:80px auto;background:var(--card);border:1px solid var(--border);border-radius:20px;padding:40px}}
.auth-card h1{{font-size:1.5rem;font-weight:800;margin:0 0 6px}}
.auth-card p{{color:var(--text2);margin:0 0 28px;font-size:.9rem}}
.form-group{{margin-bottom:18px}}
.form-group label{{display:block;font-size:.82rem;font-weight:600;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em}}
.form-group input{{width:100%;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;color:var(--text);font-size:.95rem;box-sizing:border-box;outline:none}}
.form-group input:focus{{border-color:var(--accent)}}
.btn-primary{{width:100%;background:linear-gradient(135deg,#5577f3,#4a6cf5);color:#fff;border:none;border-radius:10px;padding:12px;font-size:1rem;font-weight:600;cursor:pointer;margin-top:8px}}
.auth-footer{{text-align:center;margin-top:20px;font-size:.85rem;color:var(--text2)}}
.auth-footer a{{color:var(--accent);text-decoration:none}}
</style></head><body>
<div class="auth-card">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:28px">
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#5577f3" stroke-width="2.5"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
    <span style="font-weight:800;font-size:1.2rem">RunCore</span>
  </div>
  <h1>Welcome back</h1>
  <p>Sign in to your company account</p>
  {err_html}
  <form method="post" action="/login">
    <div class="form-group"><label>Email</label><input type="email" name="email" required autofocus></div>
    <div class="form-group"><label>Password</label><input type="password" name="password" required></div>
    <button class="btn-primary" type="submit">Sign in →</button>
  </form>
  <div class="auth-footer">Don't have an account? <a href="/register">Create one free →</a></div>
</div>
</body></html>"""


@app.post("/login")
def login_submit(email: str = Form(...), password: str = Form(...)):
    result = _store.login_tenant(email, password)
    if not result:
        return RedirectResponse("/login?error=Invalid+email+or+password", status_code=303)
    resp = RedirectResponse("/app/dashboard", status_code=303)
    resp.set_cookie("session", result["token"], max_age=30*24*3600, httponly=True, samesite="lax")
    return resp


@app.get("/register", response_class=HTMLResponse)
def register_page(error: str = ""):
    err_html = f'<div style="color:#ef4444;margin-bottom:16px;font-size:.9rem">{error}</div>' if error else ""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore — Create Account</title>
<style>{_DESIGN_CSS}
.auth-card{{max-width:440px;margin:60px auto;background:var(--card);border:1px solid var(--border);border-radius:20px;padding:40px}}
.auth-card h1{{font-size:1.5rem;font-weight:800;margin:0 0 6px}}
.auth-card p{{color:var(--text2);margin:0 0 28px;font-size:.9rem}}
.form-group{{margin-bottom:18px}}
.form-group label{{display:block;font-size:.82rem;font-weight:600;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em}}
.form-group input{{width:100%;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;color:var(--text);font-size:.95rem;box-sizing:border-box;outline:none}}
.form-group input:focus{{border-color:var(--accent)}}
.btn-primary{{width:100%;background:linear-gradient(135deg,#5577f3,#4a6cf5);color:#fff;border:none;border-radius:10px;padding:12px;font-size:1rem;font-weight:600;cursor:pointer;margin-top:8px}}
.auth-footer{{text-align:center;margin-top:20px;font-size:.85rem;color:var(--text2)}}
.auth-footer a{{color:var(--accent);text-decoration:none}}
</style></head><body>
<div class="auth-card">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:28px">
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#5577f3" stroke-width="2.5"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
    <span style="font-weight:800;font-size:1.2rem">RunCore</span>
  </div>
  <h1>Create your account</h1>
  <p>Free forever — certify your first agent in minutes</p>
  {err_html}
  <form method="post" action="/register">
    <div class="form-group"><label>Company name</label><input type="text" name="company_name" placeholder="Acme Corp" required autofocus></div>
    <div class="form-group"><label>Email</label><input type="email" name="email" required></div>
    <div class="form-group"><label>Password</label><input type="password" name="password" required minlength="8" placeholder="8+ characters"></div>
    <button class="btn-primary" type="submit">Create account →</button>
  </form>
  <div class="auth-footer">Already have an account? <a href="/login">Sign in →</a></div>
</div>
</body></html>"""


@app.post("/register")
def register_submit(company_name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    if len(password) < 8:
        return RedirectResponse("/register?error=Password+must+be+8%2B+characters", status_code=303)
    try:
        _store.register_tenant(email, password, company_name)
    except ValueError as e:
        return RedirectResponse(f"/register?error={str(e).replace(' ', '+')}", status_code=303)
    result = _store.login_tenant(email, password)
    resp = RedirectResponse("/app/dashboard", status_code=303)
    resp.set_cookie("session", result["token"], max_age=30*24*3600, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout(session: str | None = Cookie(default=None)):
    if session:
        _store.delete_session(session)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


# ---------------------------------------------------------------------------
# Company dashboard — isolated per tenant
# ---------------------------------------------------------------------------

# ===========================================================================
# Company certification jobs — background runner + status polling
# ===========================================================================

_cert_jobs: dict[str, dict] = {}
_cert_jobs_lock = threading.Lock()
_cert_env_lock = threading.Lock()   # serialize env-var mutation across concurrent runs

_PROVIDER_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "ollama": "OLLAMA_HOST"}


def _set_job(job_id: str, **fields) -> None:
    with _cert_jobs_lock:
        job = _cert_jobs.setdefault(job_id, {})
        job.update(fields)


def _get_job(job_id: str) -> dict | None:
    with _cert_jobs_lock:
        job = _cert_jobs.get(job_id)
        return dict(job) if job else None


def _run_cert_job(job_id: str, tenant: dict, cfg: dict) -> None:
    """Execute a certification in a worker thread, persist it, and email the result."""
    import os
    from benchmarks.certification import run_certification, save_cert

    company = tenant.get("company_name") or tenant.get("name", "Your Company")
    provider = cfg["provider"]
    provider_kwargs = cfg.get("provider_kwargs") or {}
    try:
        _set_job(job_id, status="running", message="Running benchmark tasks…")

        # Apply this tenant's saved provider key to the env for the duration of the
        # run (serialized so concurrent tenants don't clobber each other's keys).
        with _cert_env_lock:
            env_var = _PROVIDER_ENV.get(provider)
            had_prev, prev = False, None
            if env_var:
                key = _store.get_tenant_keys(tenant["id"]).get(provider)
                if key:
                    had_prev = env_var in os.environ
                    prev = os.environ.get(env_var)
                    os.environ[env_var] = key
            try:
                score = run_certification(
                    provider_name=provider,
                    model=cfg.get("model"),
                    runs_per_task=int(cfg.get("runs", 5)),
                    suite=cfg.get("suite", "support"),
                    verbose=False,
                    provider_kwargs=provider_kwargs,
                )
            finally:
                if env_var:
                    if had_prev:
                        os.environ[env_var] = prev
                    else:
                        os.environ.pop(env_var, None)

        out = save_cert(score)
        report_html = out.read_text(encoding="utf-8")
        cert_dict = {
            "overall": score.overall,
            "grade": score.grade,
            "certified": score.certified,
            "provider": score.provider,
            "model": score.model,
            "suite": score.suite,
            "n_runs": score.n_runs,
            "timestamp": score.timestamp,
            "product_name": cfg.get("product_name", ""),
            "dimensions": [
                {"name": d.name, "score": d.score, "improvement_pct": d.improvement_pct, "passed": d.passed}
                for d in score.dimensions
            ],
        }
        cert_id = _store.save_certification(
            tenant["id"], cert_dict, html_file=out.name,
            product_name=cfg.get("product_name", ""), cert_type=cfg.get("cert_type", "model"),
        )

        emailed = False
        try:
            from runcore.server import email_send
            emailed = email_send.send_certification_email(
                tenant.get("email", ""), company, cert_dict,
                report_html=report_html,
                label=cfg.get("product_name") or f"{score.provider} / {score.model}",
            )
        except Exception:
            emailed = False

        _set_job(
            job_id, status="done", cert_id=cert_id, score=cert_dict, emailed=emailed,
            report_url=f"/app/certify/report/{cert_id}",
            message="Certification complete",
        )
    except Exception as exc:  # noqa: BLE001
        _set_job(job_id, status="error", message=f"{type(exc).__name__}: {exc}")


@app.get("/app/dashboard", response_class=HTMLResponse)
def company_dashboard(session: str | None = Cookie(default=None)):
    tenant = _get_tenant(session)
    if not tenant:
        return RedirectResponse("/login", status_code=303)

    company = tenant.get("company_name") or tenant.get("name", "Your Company")
    certs = _store.list_certifications(tenant["id"])

    cert_rows = ""
    if not certs:
        cert_rows = '<tr><td colspan="7" style="text-align:center;padding:40px;color:var(--muted)">No certifications yet. Run your first one below.</td></tr>'
    else:
        grade_colors = {"A+": "#22c55e", "A": "#22c55e", "B+": "#60a5fa", "B": "#6488f5", "C": "#fbbf24", "F": "#ef4444"}
        for c in certs:
            grade = c.get("grade", "?")
            col = grade_colors.get(grade, "#94a3b8")
            cert_chip = '<span style="color:#22c55e;font-size:.75rem;font-weight:600">✓ Certified</span>' if c.get("certified") else '<span style="color:#94a3b8;font-size:.75rem">not certified</span>'
            cid = c.get("id", "")
            if cid:
                report_link = (f'<a href="/app/certify/report/{cid}" target="_blank" style="color:var(--accent);font-size:.8rem">view</a> '
                               f'<a href="/app/certify/report/{cid}/download" style="color:var(--muted);font-size:.8rem">↓</a>')
            else:
                report_link = "—"
            subject = c.get("product_name") or c.get("model", "?")
            type_chip = ('<span style="font-size:.65rem;color:#a78bfa;border:1px solid #a78bfa55;border-radius:4px;padding:1px 5px;margin-left:6px">agent</span>'
                         if c.get("cert_type") == "agent" else "")
            cert_rows += f"""<tr>
              <td><span style="background:{col};color:#06101f;font-weight:700;font-size:.8rem;padding:2px 8px;border-radius:5px">{grade}</span></td>
              <td style="font-weight:700">{c.get('score',0):.1f}</td>
              <td>{c.get('provider','?')}</td>
              <td style="font-size:.85rem">{subject}{type_chip}</td>
              <td style="font-size:.82rem">{c.get('suite','?')}</td>
              <td>{cert_chip}</td>
              <td>{report_link}</td>
            </tr>"""

    # Badge embed snippet for the most recent certified result
    best = next((c for c in certs if c.get("certified")), None)
    badge_card = ""
    if best:
        g = best.get("grade", "B").replace("+", "plus")
        badge_url = f"/badge/{g}.svg"
        md = f"[![RunCore Certified]({badge_url})](/leaderboard)"
        badge_card = f"""
  <div class="card" style="margin-top:24px;padding:28px">
    <h2 style="margin:0 0 8px;font-size:1rem;font-weight:700">Your Certification Badge</h2>
    <p style="color:var(--text2);font-size:.85rem;margin:0 0 14px">Embed this in your README or site to show your RunCore grade:</p>
    <div style="margin-bottom:12px"><img src="{badge_url}" alt="RunCore badge" style="height:24px"></div>
    <div style="background:#0d1117;border-radius:8px;padding:12px 14px;font-family:monospace;font-size:.78rem;color:#e6edf3;word-break:break-all">{md}</div>
  </div>"""

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore — {company}</title>
<style>{_DESIGN_CSS}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;font-size:.75rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding:10px 12px;border-bottom:1px solid var(--border)}}
td{{padding:12px;border-bottom:1px solid var(--border)22;vertical-align:middle}}
tr:hover td{{background:var(--surface)}}
.run-btn{{background:linear-gradient(135deg,#5577f3,#4a6cf5);color:#fff;border:none;border-radius:8px;padding:9px 18px;font-size:.85rem;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}}
</style></head><body>
<nav class="nav">
  <div class="nav-brand">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
    <span>RunCore</span>
  </div>
  <div class="nav-links">
    <a href="/app/dashboard" class="nav-link active">{company}</a>
    <a href="/leaderboard" class="nav-link">Leaderboard</a>
    <a href="/app/settings" class="nav-link">Settings</a>
    <a href="/logout" class="nav-link" style="color:#ef4444">Sign out</a>
  </div>
</nav>
<main class="container" style="max-width:960px;padding-top:40px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:32px">
    <div>
      <h1 style="margin:0 0 4px;font-size:1.8rem">{company}</h1>
      <div style="color:var(--text2);font-size:.9rem">Plan: <strong style="color:var(--text)">{tenant.get('plan','free').title()}</strong> · API key: <code style="background:var(--surface);padding:2px 8px;border-radius:4px;font-size:.8rem">{tenant.get('api_key','')[:20]}…</code></div>
    </div>
    <a href="/app/certify" class="run-btn">+ Run Certification</a>
  </div>

  <div class="card" style="padding:0">
    <div style="padding:20px 24px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
      <h2 style="margin:0;font-size:1rem;font-weight:700">Certification History</h2>
      <span style="color:var(--muted);font-size:.82rem">{len(certs)} run{"s" if len(certs)!=1 else ""}</span>
    </div>
    <table>
      <thead><tr><th>Grade</th><th>Score</th><th>Provider</th><th>Subject</th><th>Suite</th><th>Status</th><th>Report</th></tr></thead>
      <tbody>{cert_rows}</tbody>
    </table>
  </div>
{badge_card}
  <div class="card" style="margin-top:24px;padding:28px">
    <h2 style="margin:0 0 8px;font-size:1rem;font-weight:700">Prefer the terminal?</h2>
    <p style="color:var(--text2);font-size:.85rem;margin:0 0 14px">You can also certify from the CLI:</p>
    <div style="background:#0d1117;border-radius:8px;padding:14px 16px;font-family:monospace;font-size:.82rem;color:#e6edf3">
      pip install runcore<br>
      export GROQ_API_KEY=your_key<br>
      runcore certify --provider groq --model llama-3.3-70b-versatile --suite support
    </div>
  </div>
</main>
</body></html>"""


@app.get("/app/settings", response_class=HTMLResponse)
def company_settings(saved: str = "", session: str | None = Cookie(default=None)):
    tenant = _get_tenant(session)
    if not tenant:
        return RedirectResponse("/login", status_code=303)
    company = tenant.get("company_name") or tenant.get("name", "Your Company")
    tkeys = _store.get_tenant_keys(tenant["id"])

    def _key_status(provider: str, env_name: str) -> str:
        val = tkeys.get(provider, "")
        if val:
            masked = "••••" + val[-4:] if len(val) > 4 else "••••"
            return f'<span style="color:#22c55e;font-size:.78rem">✓ saved ({masked})</span>'
        return '<span style="color:var(--muted);font-size:.78rem">not set</span>'

    groq_status = _key_status("groq", "GROQ_API_KEY")
    gemini_status = _key_status("gemini", "GEMINI_API_KEY")
    saved_banner = ('<div style="background:#22c55e22;border:1px solid #22c55e55;color:#22c55e;'
                    'border-radius:8px;padding:10px 14px;margin-bottom:20px;font-size:.85rem">✓ Keys saved.</div>'
                    if saved else "")
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore — Settings</title>
<style>{_DESIGN_CSS}
.form-group{{margin-bottom:20px}}
.form-group label{{display:block;font-size:.82rem;font-weight:600;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em}}
.form-group input{{width:100%;max-width:480px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;color:var(--text);font-size:.9rem;box-sizing:border-box}}
.btn{{background:linear-gradient(135deg,#5577f3,#4a6cf5);color:#fff;border:none;border-radius:8px;padding:10px 20px;font-size:.9rem;font-weight:600;cursor:pointer}}
</style></head><body>
<nav class="nav">
  <div class="nav-brand">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
    <span>RunCore</span>
  </div>
  <div class="nav-links">
    <a href="/app/dashboard" class="nav-link">{company}</a>
    <a href="/app/settings" class="nav-link active">Settings</a>
    <a href="/logout" class="nav-link" style="color:#ef4444">Sign out</a>
  </div>
</nav>
<main class="container" style="max-width:700px;padding-top:40px">
  <h1 style="margin:0 0 32px;font-size:1.6rem">Account Settings</h1>
  {saved_banner}
  <div class="card" style="padding:28px;margin-bottom:24px">
    <h2 style="margin:0 0 20px;font-size:1rem;font-weight:700">Company</h2>
    <div class="form-group"><label>Company name</label><input type="text" value="{company}" readonly></div>
    <div class="form-group"><label>Email</label><input type="email" value="{tenant.get('email','')}" readonly></div>
    <div class="form-group"><label>Plan</label><input type="text" value="{tenant.get('plan','free').title()}" readonly></div>
  </div>

  <div class="card" style="padding:28px;margin-bottom:24px">
    <h2 style="margin:0 0 8px;font-size:1rem;font-weight:700">RunCore API Key</h2>
    <p style="color:var(--text2);font-size:.85rem;margin:0 0 14px">Use this key to submit certifications via the API.</p>
    <div class="form-group"><label>API Key</label>
      <input type="text" value="{tenant.get('api_key','')}" readonly style="font-family:monospace;font-size:.8rem">
    </div>
  </div>

  <div class="card" style="padding:28px">
    <h2 style="margin:0 0 8px;font-size:1rem;font-weight:700">LLM Provider Keys</h2>
    <p style="color:var(--text2);font-size:.85rem;margin:0 0 14px">Add your own Groq or Gemini keys to run certifications from the dashboard — no terminal needed. Keys are stored privately against your company. Leave a field blank to keep the current key, or enter <code>-</code> to remove it.</p>
    <form method="post" action="/app/settings/keys">
      <div class="form-group"><label>Groq API Key &nbsp; {groq_status}</label><input type="password" name="groq_key" placeholder="gsk_… (free at console.groq.com)"></div>
      <div class="form-group"><label>Gemini API Key &nbsp; {gemini_status}</label><input type="password" name="gemini_key" placeholder="AIza…"></div>
      <button class="btn" type="submit">Save keys</button>
    </form>
  </div>
</main>
</body></html>"""


@app.post("/app/settings/keys")
def save_company_keys(
    groq_key: str = Form(default=""),
    gemini_key: str = Form(default=""),
    session: str | None = Cookie(default=None),
):
    tenant = _get_tenant(session)
    if not tenant:
        return RedirectResponse("/login", status_code=303)
    # Per-tenant isolation: each company's keys are stored against their tenant id.
    # A blank field leaves an existing key untouched; "-" clears it.
    if groq_key.strip() == "-":
        _store.set_tenant_key(tenant["id"], "groq", "")
    elif groq_key.strip():
        _store.set_tenant_key(tenant["id"], "groq", groq_key.strip())
    if gemini_key.strip() == "-":
        _store.set_tenant_key(tenant["id"], "gemini", "")
    elif gemini_key.strip():
        _store.set_tenant_key(tenant["id"], "gemini", gemini_key.strip())
    return RedirectResponse("/app/settings?saved=1", status_code=303)


@app.get("/app/certify", response_class=HTMLResponse)
def company_certify_page(session: str | None = Cookie(default=None)):
    tenant = _get_tenant(session)
    if not tenant:
        return RedirectResponse("/login", status_code=303)
    company = tenant.get("company_name") or tenant.get("name", "")
    tkeys = _store.get_tenant_keys(tenant["id"])
    has_groq = "groq" in tkeys
    groq_warn = "" if has_groq else (
        '<div style="background:#f59e0b22;border:1px solid #f59e0b55;color:#f59e0b;border-radius:8px;'
        'padding:10px 14px;margin-bottom:20px;font-size:.84rem">⚠️ No Groq key saved. '
        'Add one in <a href="/app/settings" style="color:#f59e0b;text-decoration:underline">Settings</a> '
        'to certify a Groq model from here (free at console.groq.com).</div>')
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore — Run Certification</title>
<style>{_DESIGN_CSS}
.form-group{{margin-bottom:20px}}
.form-group label{{display:block;font-size:.82rem;font-weight:600;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em}}
.form-group select,.form-group input{{width:100%;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;color:var(--text);font-size:.9rem;box-sizing:border-box}}
.btn{{background:linear-gradient(135deg,#5577f3,#4a6cf5);color:#fff;border:none;border-radius:8px;padding:12px 28px;font-size:.95rem;font-weight:600;cursor:pointer}}
.btn:disabled{{opacity:.55;cursor:not-allowed}}
.tabs{{display:flex;gap:8px;margin-bottom:24px}}
.tab{{flex:1;text-align:center;padding:12px;border:1px solid var(--border);border-radius:10px;cursor:pointer;font-size:.88rem;font-weight:600;color:var(--text2);background:var(--surface)}}
.tab.active{{border-color:#5577f3;color:#fff;background:#5577f322}}
.hint{{font-size:.8rem;color:var(--muted);margin-top:-12px;margin-bottom:18px}}
</style></head><body>
<nav class="nav">
  <div class="nav-brand">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
    <span>RunCore</span>
  </div>
  <div class="nav-links">
    <a href="/app/dashboard" class="nav-link">{company}</a>
    <a href="/app/settings" class="nav-link">Settings</a>
    <a href="/logout" class="nav-link" style="color:#ef4444">Sign out</a>
  </div>
</nav>
<main class="container" style="max-width:600px;padding-top:40px">
  <h1 style="margin:0 0 8px;font-size:1.6rem">Run Certification</h1>
  <p style="color:var(--text2);margin:0 0 28px">Measures efficiency against the RunCore Score™ open benchmark. Runs in the cloud — no terminal needed.</p>

  <div class="tabs">
    <div class="tab active" id="tab-model" onclick="setMode('model')">Certify a model</div>
    <div class="tab" id="tab-agent" onclick="setMode('agent')">Bring your own agent</div>
  </div>

  <div class="card" style="padding:32px">
    {groq_warn}
    <div class="form-group">
      <label>Product / Agent name</label>
      <input id="product" type="text" placeholder="e.g. Acme Support Agent v2">
      <div class="hint">Shown on your certificate and (optionally) the leaderboard.</div>
    </div>

    <!-- MODEL MODE -->
    <div id="mode-model">
      <div class="form-group">
        <label>Provider</label>
        <select id="provider" onchange="updateModels()">
          <option value="groq">Groq (free, cloud)</option>
          <option value="ollama">Ollama (local)</option>
        </select>
      </div>
      <div class="form-group">
        <label>Model</label>
        <select id="model"></select>
      </div>
    </div>

    <!-- AGENT MODE -->
    <div id="mode-agent" style="display:none">
      <div class="form-group">
        <label>Agent endpoint (OpenAI-compatible)</label>
        <input id="agent_url" type="text" placeholder="https://your-agent.example.com/v1">
        <div class="hint">RunCore POSTs benchmark tasks to {{endpoint}}/chat/completions and measures your real agent.</div>
      </div>
      <div class="form-group">
        <label>API key / bearer token (optional)</label>
        <input id="agent_key" type="password" placeholder="sk-… (sent as Authorization: Bearer)">
      </div>
      <div class="form-group">
        <label>Model identifier your endpoint expects</label>
        <input id="agent_model" type="text" placeholder="e.g. gpt-4o-mini or your-deployment-name" value="agent">
      </div>
    </div>

    <div class="form-group">
      <label>Suite</label>
      <select id="suite">
        <option value="support">support (3 tasks, ~5 min)</option>
        <option value="all">all (8 tasks, ~15 min)</option>
        <option value="research">research (2 tasks)</option>
        <option value="coding">coding (2 tasks)</option>
      </select>
    </div>
    <div class="form-group">
      <label>Runs per task</label>
      <select id="runs">
        <option value="3">3 runs (quick)</option>
        <option value="5" selected>5 runs (standard)</option>
        <option value="10">10 runs (enterprise)</option>
      </select>
    </div>
    <button class="btn" id="go" style="margin-top:8px;width:100%" onclick="startCert()">Start Certification →</button>
  </div>

  <div id="status-box" style="display:none;margin-top:24px">
    <div class="card" style="padding:28px;text-align:center">
      <div id="spin" style="font-size:1.6rem;margin-bottom:10px">⏳</div>
      <div id="status-msg" style="font-weight:600">Starting certification…</div>
      <div id="status-sub" style="color:var(--muted);font-size:.85rem;margin-top:6px">This runs real LLM calls and can take 5–15 min. You can keep this tab open.</div>
      <div id="result" style="display:none;margin-top:18px"></div>
    </div>
  </div>
</main>
<script>
let mode = "model";
const groqModels = ["llama-3.3-70b-versatile","llama-3.1-8b-instant","mixtral-8x7b-32768","gemma2-9b-it"];
const ollamaModels = ["qwen2.5:14b","qwen2.5:7b","llama3.1:8b","llama3.2"];
function updateModels() {{
  const p = document.getElementById("provider").value;
  const sel = document.getElementById("model");
  const models = p === "groq" ? groqModels : ollamaModels;
  sel.innerHTML = models.map((m,i) => `<option value="${{m}}">${{m}}${{i===0?" (recommended)":""}}</option>`).join("");
}}
function setMode(m) {{
  mode = m;
  document.getElementById("tab-model").classList.toggle("active", m==="model");
  document.getElementById("tab-agent").classList.toggle("active", m==="agent");
  document.getElementById("mode-model").style.display = m==="model" ? "block" : "none";
  document.getElementById("mode-agent").style.display = m==="agent" ? "block" : "none";
}}
updateModels();
function poll(jobId) {{
  fetch("/app/certify/status/" + jobId).then(r => r.json()).then(d => {{
    if (d.status === "running") {{
      document.getElementById("status-msg").textContent = d.message || "Running benchmark tasks…";
      setTimeout(() => poll(jobId), 3000);
    }} else if (d.status === "done") {{
      const s = d.score || {{}};
      document.getElementById("spin").textContent = "✅";
      document.getElementById("status-msg").textContent = "Certification complete — " + (s.grade||"") + " · " + (s.overall||0).toFixed(1) + "/100";
      document.getElementById("status-sub").textContent = d.emailed ? "A copy was emailed to you." : "";
      const res = document.getElementById("result");
      res.style.display = "block";
      res.innerHTML = '<a class="btn" href="'+d.report_url+'" target="_blank">View report →</a> ' +
                      '<a class="btn" style="background:#1b2333" href="/app/dashboard">Back to dashboard</a>';
    }} else if (d.status === "error") {{
      document.getElementById("spin").textContent = "⚠️";
      document.getElementById("status-msg").textContent = "Error";
      document.getElementById("status-sub").textContent = d.message || "Certification failed.";
      document.getElementById("go").disabled = false;
    }} else {{
      setTimeout(() => poll(jobId), 3000);
    }}
  }}).catch(() => setTimeout(() => poll(jobId), 4000));
}}
function startCert() {{
  const body = {{
    mode: mode,
    product_name: document.getElementById("product").value,
    suite: document.getElementById("suite").value,
    runs_per_task: parseInt(document.getElementById("runs").value),
  }};
  if (mode === "model") {{
    body.provider = document.getElementById("provider").value;
    body.model = document.getElementById("model").value;
  }} else {{
    body.agent_url = document.getElementById("agent_url").value.trim();
    body.agent_key = document.getElementById("agent_key").value;
    body.model = document.getElementById("agent_model").value.trim() || "agent";
    if (!body.agent_url) {{ alert("Enter your agent endpoint URL."); return; }}
  }}
  document.getElementById("go").disabled = true;
  document.getElementById("status-box").style.display = "block";
  document.getElementById("status-msg").textContent = "Starting certification…";
  fetch("/app/certify/run", {{
    method: "POST", headers: {{"Content-Type":"application/json"}}, body: JSON.stringify(body)
  }}).then(r => r.json()).then(d => {{
    if (d.job_id) {{ poll(d.job_id); }}
    else {{
      document.getElementById("spin").textContent = "⚠️";
      document.getElementById("status-msg").textContent = "Error";
      document.getElementById("status-sub").textContent = d.detail || "Could not start certification.";
      document.getElementById("go").disabled = false;
    }}
  }}).catch(e => {{
    document.getElementById("status-msg").textContent = "Error: " + e.message;
    document.getElementById("go").disabled = false;
  }});
}}
</script>
</body></html>"""


@app.post("/app/certify/run")
async def app_certify_run(request: Request, session: str | None = Cookie(default=None)) -> dict:
    """Start a tenant-scoped certification in the background. Returns a job_id to poll."""
    tenant = _get_tenant(session)
    if not tenant:
        raise HTTPException(status_code=401, detail="Not signed in")

    body = await request.json()
    mode = body.get("mode", "model")
    suite = body.get("suite", "support")
    runs = int(body.get("runs_per_task", 5))
    product_name = (body.get("product_name") or "").strip()

    cfg: dict = {"suite": suite, "runs": runs, "product_name": product_name}

    if mode == "agent":
        agent_url = (body.get("agent_url") or "").strip()
        if not agent_url:
            raise HTTPException(status_code=400, detail="Agent endpoint URL is required.")
        cfg["provider"] = "http"
        cfg["model"] = (body.get("model") or "agent").strip()
        cfg["cert_type"] = "agent"
        cfg["provider_kwargs"] = {
            "base_url": agent_url,
            "api_key": body.get("agent_key") or "",
        }
    else:
        provider = body.get("provider", "groq")
        cfg["provider"] = provider
        cfg["model"] = body.get("model") or None
        cfg["cert_type"] = "model"
        # Require the tenant to have the matching key saved (Groq/Gemini).
        if provider in ("groq", "gemini"):
            if provider not in _store.get_tenant_keys(tenant["id"]):
                raise HTTPException(
                    status_code=400,
                    detail=f"No {provider} key saved. Add one in Settings first.",
                )

    job_id = uuid.uuid4().hex
    _set_job(job_id, status="queued", message="Queued…", tenant_id=tenant["id"])
    # Snapshot the tenant dict so the worker thread doesn't touch request state.
    tenant_snapshot = dict(tenant)
    threading.Thread(target=_run_cert_job, args=(job_id, tenant_snapshot, cfg), daemon=True).start()
    return {"job_id": job_id}


@app.get("/app/certify/status/{job_id}")
def app_certify_status(job_id: str, session: str | None = Cookie(default=None)) -> dict:
    tenant = _get_tenant(session)
    if not tenant:
        raise HTTPException(status_code=401, detail="Not signed in")
    job = _get_job(job_id)
    if not job or job.get("tenant_id") != tenant["id"]:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "status": job.get("status", "unknown"),
        "message": job.get("message", ""),
        "score": job.get("score"),
        "report_url": job.get("report_url"),
        "emailed": job.get("emailed", False),
        "cert_id": job.get("cert_id"),
    }


def _serve_tenant_report(tenant: dict, cert_id: str, download: bool):
    from fastapi import HTTPException as _HTTPExc
    from benchmarks.certification import RESULTS_DIR
    cert = _store.get_certification(tenant["id"], cert_id)
    if not cert or not cert.get("html_file"):
        raise _HTTPExc(status_code=404, detail="Report not found")
    path = RESULTS_DIR / "certifications" / cert["html_file"]
    if not path.exists():
        raise _HTTPExc(status_code=404, detail="Report file missing")
    html = path.read_text(encoding="utf-8")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="runcore_certificate_{cert.get("grade","").replace("+","plus")}.html"'
    return Response(content=html, media_type="text/html", headers=headers)


@app.get("/app/certify/report/{cert_id}", response_class=HTMLResponse)
def app_certify_report(cert_id: str, session: str | None = Cookie(default=None)):
    tenant = _get_tenant(session)
    if not tenant:
        return RedirectResponse("/login", status_code=303)
    return _serve_tenant_report(tenant, cert_id, download=False)


@app.get("/app/certify/report/{cert_id}/download")
def app_certify_report_download(cert_id: str, session: str | None = Cookie(default=None)):
    tenant = _get_tenant(session)
    if not tenant:
        return RedirectResponse("/login", status_code=303)
    return _serve_tenant_report(tenant, cert_id, download=True)
