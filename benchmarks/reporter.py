"""RunCore benchmark HTML reporter — generates a showable before/after comparison."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime


def _pct_color(pct: float) -> str:
    """Color code a percentage: negative = green (savings), positive = bad."""
    if pct <= -10:
        return "#22c55e"
    elif pct <= 0:
        return "#86efac"
    elif pct <= 10:
        return "#fbbf24"
    else:
        return "#ef4444"


def _improvement_badge(pct: float) -> str:
    sign = "▼" if pct < 0 else "▲"
    color = _pct_color(pct)
    return f'<span style="color:{color};font-weight:bold">{sign}{abs(pct):.1f}%</span>'


def generate_html(summary: dict) -> str:
    b = summary.get("baseline", {})
    g = summary.get("guarded", {})
    imp = summary.get("improvements", {})
    provider = summary.get("provider", "unknown")
    model = summary.get("model") or "default"
    suite = summary.get("suite", "unknown")
    ts = summary.get("timestamp", "")
    advisor = summary.get("advisor") or {}
    prescriptions = advisor.get("prescriptions", [])

    # Format timestamp
    try:
        dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")
        ts_human = dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        ts_human = ts

    cpst_savings = imp.get("cpst_reduction_pct", 0)
    cost_savings = imp.get("cost_reduction_pct", 0)
    token_savings = imp.get("token_reduction_pct", 0)
    dups_blocked = imp.get("duplicates_blocked", 0)

    rows = []
    metrics = [
        ("Avg CpST (USD)", f"${b.get('avg_cpst', 0):.6f}", f"${g.get('avg_cpst', 0):.6f}", cpst_savings),
        ("Avg Cost / Run (USD)", f"${b.get('avg_cost_usd', 0):.6f}", f"${g.get('avg_cost_usd', 0):.6f}", cost_savings),
        ("Avg Tokens", f"{b.get('avg_tokens', 0):.0f}", f"{g.get('avg_tokens', 0):.0f}", token_savings),
        ("Total Duplicate Calls", str(b.get('total_duplicates', 0)), str(g.get('total_duplicates', 0)), None),
        ("Success Rate", f"{b.get('success_rate_pct', 0):.1f}%", f"{g.get('success_rate_pct', 0):.1f}%", None),
        ("Runs", str(b.get('runs', 0)), str(g.get('runs', 0)), None),
    ]

    for label, bv, gv, pct in metrics:
        badge = _improvement_badge(pct) if pct is not None else ""
        rows.append(f"""
        <tr>
            <td>{label}</td>
            <td class="num">{bv}</td>
            <td class="num good">{gv}</td>
            <td class="num">{badge}</td>
        </tr>""")

    prescription_html = ""
    if prescriptions:
        items = ""
        for p in prescriptions[:6]:
            effort_color = {"low": "#22c55e", "medium": "#fbbf24", "high": "#ef4444"}.get(p.get("effort", ""), "#888")
            items += f"""
            <div class="prescription">
                <div class="p-header">
                    <span class="p-title">{p.get('title', '')}</span>
                    <span class="p-savings">~{p.get('estimated_savings_pct', 0):.0f}% savings</span>
                    <span class="p-effort" style="color:{effort_color}">{p.get('effort','?')} effort</span>
                </div>
                <div class="p-desc">{p.get('description', '')}</div>
            </div>"""
        total = advisor.get("total_estimated_savings_pct", 0)
        prescription_html = f"""
        <section class="section">
            <h2>OptimizationAdvisor — {len(prescriptions)} Prescriptions ({total:.1f}% combined savings)</h2>
            {items}
        </section>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RunCore Benchmark — {provider} / {suite}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; }}
  .container {{ max-width: 900px; margin: 0 auto; padding: 2rem; }}
  header {{ border-bottom: 1px solid #334155; padding-bottom: 1.5rem; margin-bottom: 2rem; }}
  h1 {{ font-size: 1.8rem; font-weight: 700; color: #f1f5f9; }}
  h1 span {{ color: #6366f1; }}
  .meta {{ color: #94a3b8; font-size: 0.9rem; margin-top: 0.4rem; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-bottom: 2rem; }}
  .kpi {{ background: #1e293b; border-radius: 10px; padding: 1.2rem; border: 1px solid #334155; }}
  .kpi-label {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: .08em; color: #64748b; }}
  .kpi-value {{ font-size: 2rem; font-weight: 700; margin: 0.3rem 0; }}
  .kpi-sub {{ font-size: 0.8rem; color: #94a3b8; }}
  .green {{ color: #22c55e; }}
  .section {{ background: #1e293b; border-radius: 10px; border: 1px solid #334155; margin-bottom: 1.5rem; overflow: hidden; }}
  .section h2 {{ padding: 1rem 1.2rem; font-size: 1rem; font-weight: 600; background: #0f172a; border-bottom: 1px solid #334155; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 0.7rem 1.2rem; text-align: left; border-bottom: 1px solid #1e293b; }}
  th {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: .05em; color: #64748b; background: #0f172a; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; font-family: monospace; font-size: 0.95rem; }}
  td.good {{ color: #86efac; }}
  .prescription {{ padding: 0.9rem 1.2rem; border-bottom: 1px solid #0f172a; }}
  .p-header {{ display: flex; align-items: center; gap: 1rem; margin-bottom: 0.3rem; }}
  .p-title {{ font-weight: 600; flex: 1; }}
  .p-savings {{ font-size: 0.85rem; color: #22c55e; }}
  .p-effort {{ font-size: 0.8rem; font-weight: 600; }}
  .p-desc {{ font-size: 0.85rem; color: #94a3b8; }}
  .badge {{ display: inline-block; padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
  .badge-guard {{ background: #1d4ed8; color: #bfdbfe; }}
  .badge-base {{ background: #374151; color: #d1d5db; }}
  footer {{ color: #475569; font-size: 0.8rem; text-align: center; margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #334155; }}
</style>
</head>
<body>
<div class="container">
<header>
  <h1>RunCore <span>Benchmark Results</span></h1>
  <div class="meta">{provider} / {model} &nbsp;·&nbsp; suite: {suite} &nbsp;·&nbsp; {ts_human}</div>
</header>

<div class="kpi-grid">
  <div class="kpi">
    <div class="kpi-label">CpST Reduction</div>
    <div class="kpi-value green">{abs(cpst_savings):.1f}%</div>
    <div class="kpi-sub">Cost per Successful Task</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Duplicates Blocked</div>
    <div class="kpi-value green">{dups_blocked}</div>
    <div class="kpi-sub">Tool calls eliminated</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Token Savings</div>
    <div class="kpi-value green">{abs(token_savings):.1f}%</div>
    <div class="kpi-sub">Context compression + dedup</div>
  </div>
</div>

<section class="section">
  <h2>Baseline vs Guarded Comparison</h2>
  <table>
    <thead><tr><th>Metric</th><th>Baseline</th><th>Guarded</th><th>Change</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</section>

{prescription_html}

<footer>Generated by <strong>RunCore v0.9.0</strong> — AI Agent Runtime Optimization &nbsp;|&nbsp; <a href="https://github.com/ptpaulinho/RunCore" style="color:#6366f1">github.com/ptpaulinho/RunCore</a></footer>
</div>
</body>
</html>"""
    return html


def save_report(summary: dict, output_path: Path | None = None) -> Path:
    results_dir = summary.get("results_dir", ".")
    if output_path is None:
        output_path = Path(results_dir) / "report.html"

    html = generate_html(summary)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def generate_from_dir(run_dir: str | Path) -> Path:
    """Load summary.json from a results directory and regenerate the HTML report."""
    run_dir = Path(run_dir)
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text())
    return save_report(summary, run_dir / "report.html")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        path = generate_from_dir(sys.argv[1])
        print(f"Report generated: {path}")
    else:
        print("Usage: python benchmarks/reporter.py <results_dir>")
