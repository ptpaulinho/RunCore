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
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from runcore.agents.simulated import SimulatedAgentFactory
from runcore.benchmark.runner import BenchmarkRunner
from runcore.benchmark.metrics import calculate_metrics
from runcore.benchmark.comparison import BenchmarkComparison
from runcore.core.models import OptimizationConfig
from runcore.reports.generator import ReportGenerator

app = FastAPI(title="RunCore Dashboard", version="0.1.0")

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
              <span>·</span>
              <span class="p-badge {badge_cls}">{badge_label}</span>
              <span>·</span>
              <span class="p-effort" style="color:{effort_color}">effort: {effort}</span>
              <span>·</span>
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
      <div class="advice-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        OptimizationAdvisor — {agent} · {n} traces analyzed · combined ~{total_pct:.1f}% estimated savings
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
<title>RunCore Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {{
  --bg: #0c0e14;
  --surface: #131720;
  --surface2: #1a1f2e;
  --border: #252d3d;
  --text: #e2e8f0;
  --muted: #64748b;
  --accent: #7c3aed;
  --accent-light: #a78bfa;
  --green: #4ade80;
  --red: #f87171;
  --yellow: #fbbf24;
  --blue: #60a5fa;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }}

/* Navbar */
.nav {{ background: var(--surface); border-bottom: 1px solid var(--border); padding: 0 32px; display: flex; align-items: center; height: 56px; gap: 32px; position: sticky; top: 0; z-index: 100; }}
.nav-logo {{ font-size: 1.25rem; font-weight: 800; color: var(--accent); letter-spacing: -0.5px; }}
.nav-logo span {{ color: var(--accent-light); }}
.nav-status {{ display: flex; align-items: center; gap: 8px; font-size: .82rem; color: var(--muted); margin-left: auto; }}
.pulse {{ width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }}
.pulse.idle {{ background: var(--muted); animation: none; }}
@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.3; }} }}

/* Layout */
.page {{ padding: 28px 32px; max-width: 1400px; margin: 0 auto; }}

/* KPI strip */
.kpi-grid {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 24px; }}
.kpi {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px 24px; }}
.kpi-label {{ font-size: .75rem; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 8px; }}
.kpi-value {{ font-size: 2rem; font-weight: 700; line-height: 1; }}
.kpi-sub {{ font-size: .78rem; color: var(--muted); margin-top: 6px; }}

/* Main grid */
.main-grid {{ display: grid; grid-template-columns: 340px 1fr; gap: 20px; margin-bottom: 24px; }}

/* Cards */
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 24px; }}
.card-title {{ font-size: .88rem; font-weight: 600; color: var(--accent-light); margin-bottom: 20px; display: flex; align-items: center; gap: 8px; }}
.card-title svg {{ opacity:.7; }}

/* Form */
.field {{ margin-bottom: 14px; }}
.field label {{ display: block; font-size: .75rem; text-transform: uppercase; letter-spacing: .8px; color: var(--muted); margin-bottom: 6px; }}
.field input, .field select, .field textarea {{
  width: 100%; background: var(--surface2); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text); padding: 9px 12px; font-size: .88rem; outline: none; transition: border-color .15s;
}}
.field input:focus, .field select:focus, .field textarea:focus {{ border-color: var(--accent); }}
.field textarea {{ height: 88px; resize: vertical; font-family: 'SF Mono', 'Fira Code', monospace; font-size: .82rem; line-height: 1.5; }}
.form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.btn-run {{
  width: 100%; padding: 11px; background: var(--accent); color: #fff;
  border: none; border-radius: 8px; font-size: .92rem; font-weight: 600; cursor: pointer;
  margin-top: 4px; transition: background .15s; display: flex; align-items: center; justify-content: center; gap: 8px;
}}
.btn-run:hover {{ background: #6d28d9; }}
.btn-run:disabled {{ background: #374151; cursor: not-allowed; }}
.run-msg {{ font-size: .82rem; color: var(--green); margin-top: 10px; min-height: 18px; text-align: center; }}
.run-msg.err {{ color: var(--red); }}

/* Charts */
.charts-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.chart-wrap {{ position: relative; height: 200px; }}
.chart-empty {{ display: flex; align-items: center; justify-content: center; height: 200px; color: var(--muted); font-size: .85rem; flex-direction: column; gap: 8px; }}
.chart-empty svg {{ opacity: .3; }}

/* History table */
.filters {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
.filter-group {{ display: flex; align-items: center; gap: 6px; }}
.filter-group label {{ font-size: .78rem; color: var(--muted); white-space: nowrap; }}
.filter-group select, .filter-group input {{
  background: var(--surface2); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text); padding: 5px 9px; font-size: .8rem; outline: none;
}}
.btn-reset {{ padding: 5px 12px; background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; color: var(--muted); font-size: .8rem; cursor: pointer; }}
.btn-reset:hover {{ color: var(--text); }}
.f-count {{ font-size: .78rem; color: var(--muted); margin-left: auto; }}

table {{ width: 100%; border-collapse: collapse; font-size: .84rem; }}
thead th {{ text-align: left; padding: 8px 14px; font-size: .72rem; text-transform: uppercase; letter-spacing: .8px; color: var(--muted); border-bottom: 1px solid var(--border); white-space: nowrap; }}
tbody td {{ padding: 11px 14px; border-bottom: 1px solid rgba(37,45,61,.5); vertical-align: middle; }}
tbody tr:last-child td {{ border-bottom: none; }}
tbody tr:hover td {{ background: rgba(124,58,237,.04); }}

/* Badges */
.badge {{ display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 20px; font-size: .75rem; font-weight: 600; }}
.badge-pass {{ background: rgba(74,222,128,.12); color: var(--green); border: 1px solid rgba(74,222,128,.2); }}
.badge-fail {{ background: rgba(248,113,113,.12); color: var(--red); border: 1px solid rgba(248,113,113,.2); }}
.badge-error {{ background: rgba(248,113,113,.08); color: #fca5a5; border: 1px solid rgba(248,113,113,.15); }}
.badge-running {{ background: rgba(251,191,36,.1); color: var(--yellow); border: 1px solid rgba(251,191,36,.2); }}
.badge-queued {{ background: rgba(100,116,139,.1); color: var(--muted); border: 1px solid var(--border); }}
.badge-dash {{ color: var(--muted); font-size: .82rem; }}

/* Savings bar */
.savings-bar {{ display: flex; align-items: center; gap: 8px; }}
.bar-bg {{ flex: 1; height: 6px; background: var(--surface2); border-radius: 3px; overflow: hidden; max-width: 80px; }}
.bar-fill {{ height: 100%; border-radius: 3px; transition: width .3s; }}

/* Error details */
details summary {{ cursor: pointer; color: var(--red); font-size: .8rem; list-style: none; display: flex; align-items: center; gap: 4px; }}
details summary::-webkit-details-marker {{ display: none; }}
details[open] summary {{ margin-bottom: 6px; }}
.error-text {{ font-family: 'SF Mono', monospace; font-size: .76rem; background: rgba(248,113,113,.06); border: 1px solid rgba(248,113,113,.15); border-radius: 6px; padding: 8px 10px; color: #fca5a5; line-height: 1.5; white-space: pre-wrap; word-break: break-all; max-width: 320px; }}

/* Spinner */
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.spinner {{ width: 14px; height: 14px; border: 2px solid rgba(251,191,36,.3); border-top-color: var(--yellow); border-radius: 50%; animation: spin .8s linear infinite; display: inline-block; }}

.empty-state {{ text-align: center; padding: 48px 24px; color: var(--muted); }}
.empty-state p {{ font-size: .9rem; margin-top: 8px; }}
.no-match {{ text-align: center; padding: 32px; color: var(--muted); font-size: .88rem; display: none; }}

a {{ color: var(--accent-light); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

.refresh-dot {{ display: inline-block; width: 6px; height: 6px; background: var(--muted); border-radius: 50%; margin-left: 4px; animation: pulse 5s linear infinite; }}

/* Advice panel */
.advice-panel {{ margin-top: 20px; }}
.advice-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px 24px; }}
.advice-title {{ font-size: .88rem; font-weight: 600; color: #a78bfa; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }}
.prescription {{ display: flex; gap: 14px; padding: 12px 0; border-bottom: 1px solid rgba(37,45,61,.5); }}
.prescription:last-child {{ border-bottom: none; }}
.prescription-rank {{ font-size: 1.2rem; font-weight: 700; color: var(--accent-light); width: 24px; flex-shrink: 0; }}
.prescription-body {{ flex: 1; min-width: 0; }}
.prescription-title {{ font-size: .86rem; font-weight: 600; color: var(--text); margin-bottom: 4px; }}
.prescription-meta {{ display: flex; gap: 10px; flex-wrap: wrap; font-size: .75rem; color: var(--muted); margin-bottom: 6px; }}
.prescription-meta .p-savings {{ color: var(--green); font-weight: 600; }}
.prescription-meta .p-effort {{ color: var(--yellow); }}
.prescription-desc {{ font-size: .8rem; color: #94a3b8; line-height: 1.5; }}
.p-badge {{ display: inline-block; padding: 2px 7px; border-radius: 10px; font-size: .72rem; font-weight: 600; }}
.p-badge-dedup {{ background: rgba(96,165,250,.1); color: #93c5fd; }}
.p-badge-context {{ background: rgba(167,139,250,.1); color: #c4b5fd; }}
.p-badge-schema {{ background: rgba(52,211,153,.1); color: #6ee7b7; }}
.p-badge-replace {{ background: rgba(251,191,36,.1); color: #fcd34d; }}
.p-badge-loop {{ background: rgba(248,113,113,.1); color: #fca5a5; }}
.p-badge-cache {{ background: rgba(74,222,128,.1); color: #86efac; }}
.advice-summary {{ font-size: .82rem; color: var(--muted); margin-bottom: 14px; line-height: 1.5; padding: 10px 14px; background: rgba(124,58,237,.05); border-radius: 8px; border-left: 3px solid var(--accent); }}
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-logo">Run<span>Core</span></div>
  <div class="nav-status">
    {'<div class="pulse"></div> <span>' + str(running_count) + ' running</span>' if running_count else '<div class="pulse idle"></div> <span>idle</span>'}
    &nbsp;·&nbsp; auto-refresh 5s <span class="refresh-dot"></span>
  </div>
</nav>

<div class="page">

  <!-- KPI strip -->
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Total Runs</div>
      <div class="kpi-value" style="color:var(--accent-light)">{total_runs}</div>
      <div class="kpi-sub">{len(pass_runs)} passed · {total_runs - len(pass_runs)} failed/error</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Avg Cost Savings</div>
      <div class="kpi-value" style="color:{kpi_savings_color}">{avg_savings:.1f}%</div>
      <div class="kpi-sub">target ≥25% · best {best_savings:.1f}%</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Avg Token Reduction</div>
      <div class="kpi-value" style="color:var(--blue)">{avg_tokens:.1f}%</div>
      <div class="kpi-sub">fewer tokens sent to LLM</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Pass Rate</div>
      <div class="kpi-value" style="color:{kpi_pass_color}">{pass_rate:.0f}%</div>
      <div class="kpi-sub">{len(pass_runs)} of {total_runs} runs met target</div>
    </div>
  </div>

  <!-- Main grid: form + charts -->
  <div class="main-grid">

    <!-- Form -->
    <div class="card">
      <div class="card-title">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5,3 19,12 5,21"/></svg>
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
        </div>
        <div class="form-row">
          <div class="field">
            <label>Runs per task</label>
            <input type="number" id="runs" value="10" min="1" max="100">
          </div>
          <div class="field">
            <label>Cost target %</label>
            <input type="number" id="target" value="25" min="1" max="90">
          </div>
        </div>
        <div class="field">
          <label>Tasks (one per line)</label>
          <textarea id="tasks">Refund invoice #1001 for customer@example.com
Check order status for john@example.com</textarea>
        </div>
        <button class="btn-run" type="submit" id="run-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5,3 19,12 5,21"/></svg>
          Run Benchmark
        </button>
        <div class="run-msg" id="run-msg"></div>
        <!-- Live progress bar (hidden until run starts) -->
        <div id="live-progress" style="display:none;margin-top:12px">
          <div style="display:flex;justify-content:space-between;font-size:.76rem;color:var(--muted);margin-bottom:4px">
            <span id="live-phase">Initializing…</span>
            <span id="live-pct">0%</span>
          </div>
          <div style="height:4px;background:var(--surface2);border-radius:2px;overflow:hidden">
            <div id="live-bar" style="height:100%;background:var(--accent);border-radius:2px;width:0%;transition:width .4s ease"></div>
          </div>
        </div>
      </form>
    </div>

    <!-- Charts stacked -->
    <div style="display:flex;flex-direction:column;gap:16px">
      <div class="card" style="flex:1">
        <div class="card-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22,12 18,12 15,21 9,3 6,12 2,12"/></svg>
          Cost per run — Baseline vs Optimized
        </div>
        {'<div class="chart-wrap"><canvas id="costChart"></canvas></div>' if done_runs else '<div class="chart-empty"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><polyline points="3,9 9,9 12,6 16,15 19,12"/></svg><span>Run a benchmark to see trends</span></div>'}
      </div>
      <div class="card" style="flex:1">
        <div class="card-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          Cost savings % — target line at 25%
        </div>
        {'<div class="chart-wrap"><canvas id="savingsChart"></canvas></div>' if done_runs else '<div class="chart-empty"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="15" x2="9" y2="17"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="15" y1="8" x2="15" y2="17"/></svg><span>Run a benchmark to see savings</span></div>'}
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
    {'' if rows else '<div class="empty-state"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="opacity:.3;margin:0 auto"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg><p>No benchmark runs yet — use the form above to start one.</p></div>'}
  </div>

  {_build_advice_panel(runs_snapshot)}

</div><!-- /page -->

<script>
const hasData = {has_chart};
const labels  = {chart_labels};
const baseline  = {chart_baseline};
const optimized = {chart_optimized};
const savings   = {chart_savings};

const GRID = {{ color: 'rgba(255,255,255,0.04)' }};
const TICK = {{ color: '#64748b', size: 11 }};

if (hasData && labels.length > 0) {{
  new Chart(document.getElementById('costChart'), {{
    type: 'line',
    data: {{
      labels,
      datasets: [
        {{ label: 'Baseline', data: baseline, borderColor: '#f87171', backgroundColor: 'rgba(248,113,113,0.06)', tension: 0.35, pointRadius: 4, pointHoverRadius: 6, fill: true }},
        {{ label: 'Optimized', data: optimized, borderColor: '#4ade80', backgroundColor: 'rgba(74,222,128,0.06)', tension: 0.35, pointRadius: 4, pointHoverRadius: 6, fill: true }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '#94a3b8', usePointStyle: true, pointStyle: 'circle', padding: 20, font: {{ size: 11 }} }} }},
        tooltip: {{
          backgroundColor: '#1a1f2e', borderColor: '#252d3d', borderWidth: 1,
          titleColor: '#e2e8f0', bodyColor: '#94a3b8', padding: 12,
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
        backgroundColor: savings.map(v => v >= 25 ? 'rgba(74,222,128,0.65)' : v >= 15 ? 'rgba(251,191,36,0.65)' : 'rgba(248,113,113,0.65)'),
        borderRadius: 5,
        borderSkipped: false,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ labels: {{ color: '#94a3b8', usePointStyle: true, pointStyle: 'circle', padding: 20, font: {{ size: 11 }} }} }},
        tooltip: {{
          backgroundColor: '#1a1f2e', borderColor: '#252d3d', borderWidth: 1,
          titleColor: '#e2e8f0', bodyColor: '#94a3b8', padding: 12,
          callbacks: {{ label: ctx => ` Savings: ${{ctx.parsed.y.toFixed(1)}}%` }}
        }},
        annotation: {{ annotations: {{ target: {{
          type: 'line', yMin: 25, yMax: 25,
          borderColor: 'rgba(167,139,250,0.5)', borderWidth: 1.5, borderDash: [5,4],
          label: {{ content: 'target 25%', enabled: true, color: '#a78bfa', font: {{ size: 10 }}, position: 'end' }}
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
      barEl.style.background = 'var(--green)';
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

// -- Auto refresh (only when not running a live benchmark) --
let _liveRunning = false;
document.getElementById('bform').addEventListener('submit', () => {{ _liveRunning = true; }});
setTimeout(() => {{ if (!_liveRunning) location.reload(); }}, 5000);
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
            # Send current state immediately
            with _lock:
                run = _runs.get(run_id, {})
            yield f"event: status\ndata: {json.dumps({'status': run.get('status', 'unknown'), 'pct': 0})}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = q.get(timeout=0.5)
                    yield msg
                    if '"status": "done"' in msg or '"status": "error"' in msg:
                        break
                except queue.Empty:
                    # Keep-alive comment
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

    The comparison is run synchronously (blocks until complete) for simplicity.
    Use the ``/benchmark`` endpoint for background runs.
    """
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

    # Determine winner by CpST (lower is better)
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
