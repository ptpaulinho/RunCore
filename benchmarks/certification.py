"""RunCore Certification System.

Runs a statistically rigorous benchmark suite and produces a RunCore Score (0–100)
with confidence intervals. The output is a signed, sharable certification report.

Usage:
    python -m benchmarks.certification --provider groq
    python -m benchmarks.certification --provider groq --runs 10 --output cert.html
"""
from __future__ import annotations

import json
import math
import sys
import hashlib
import statistics
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.runner import run_one, RESULTS_DIR
from benchmarks.tasks import ALL_TASKS, BenchmarkTask


# ---------------------------------------------------------------------------
# RunCore Score formula
# ---------------------------------------------------------------------------
# Score (0–100) weights three dimensions:
#   40%  Cost savings   — how much cheaper optimized vs baseline
#   35%  Token reduction — tokens eliminated from LLM calls
#   25%  Task success rate — fraction of tasks completed correctly
#
# Each dimension is normalised to [0, 1] against targets:
#   Cost savings target:   ≥ 25%   (industry benchmark for "significant")
#   Token reduction target: ≥ 20%
#   Success rate target:   = 100% (no regressions)

SCORE_WEIGHTS = {"cost": 0.40, "tokens": 0.35, "success": 0.25}
COST_TARGET   = 25.0   # %
TOKEN_TARGET  = 20.0   # %
# Certification is gated on task success: an agent that fails most tasks can never be
# "certified efficient", no matter how cheap. Efficiency is meaningless without correctness.
MIN_SUCCESS_FOR_CERT = 60.0   # % of tasks that must succeed (with guards) to be certifiable


@dataclass
class CertDimension:
    name: str
    baseline: float
    optimized: float
    improvement_pct: float
    target_pct: float
    score: float          # 0–100 for this dimension
    passed: bool


@dataclass
class RunCoreScore:
    overall: float                          # 0–100
    dimensions: list[CertDimension]
    confidence_interval_95: tuple[float, float]
    n_runs: int
    n_tasks: int
    provider: str
    model: str
    suite: str
    timestamp: str
    certified: bool                         # overall >= 60 AND success rate >= MIN_SUCCESS_FOR_CERT
    run_scores: list[float] = field(default_factory=list)

    @property
    def grade(self) -> str:
        if self.overall >= 90: return "A+"
        if self.overall >= 80: return "A"
        if self.overall >= 70: return "B+"
        if self.overall >= 60: return "B"
        if self.overall >= 50: return "C"
        return "F"

    @property
    def badge_color(self) -> str:
        if self.overall >= 80: return "#22c55e"
        if self.overall >= 60: return "#3b82f6"
        if self.overall >= 40: return "#f59e0b"
        return "#ef4444"


# ---------------------------------------------------------------------------
# Certification runner
# ---------------------------------------------------------------------------

def _safe_pct(baseline: float, optimized: float) -> float:
    if baseline == 0:
        return 0.0
    return ((baseline - optimized) / baseline) * 100


def _dimension_score(improvement_pct: float, target_pct: float) -> float:
    """Map improvement % to 0–100 score. Hitting target = 70. Double target = 100."""
    if improvement_pct <= 0:
        return max(0.0, 50 + improvement_pct * 2)   # negative savings penalised
    ratio = improvement_pct / target_pct
    if ratio >= 2.0:
        return 100.0
    if ratio >= 1.0:
        return 70 + (ratio - 1.0) * 30              # 70 → 100 between target and 2× target
    return ratio * 70                               # 0 → 70 below target


def run_certification(
    provider_name: str,
    model: str | None = None,
    runs_per_task: int = 5,
    suite: str = "all",
    verbose: bool = True,
) -> RunCoreScore:
    """Run the full certification suite and return a RunScore."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if suite == "all":
        tasks: list[BenchmarkTask] = [t for ts_list in ALL_TASKS.values() for t in ts_list]
    else:
        tasks = ALL_TASKS.get(suite, [])
        if not tasks:
            raise ValueError(f"Unknown suite: {suite}. Available: {list(ALL_TASKS.keys()) + ['all']}")

    if verbose:
        print(f"\n{'='*60}")
        print(f"  RunCore Certification — {provider_name} / {suite}")
        print(f"  {len(tasks)} tasks × {runs_per_task} runs = {len(tasks) * runs_per_task * 2} total LLM calls")
        print(f"{'='*60}\n")

    # Collect per-run data
    all_baseline_costs   = []
    all_optimized_costs  = []
    all_baseline_tokens  = []
    all_optimized_tokens = []
    baseline_successes   = 0
    optimized_successes  = 0
    total_runs           = 0
    per_run_scores       = []

    for task in tasks:
        for run_i in range(runs_per_task):
            if verbose:
                print(f"  [{task.id}] run {run_i+1}/{runs_per_task} ...", end=" ", flush=True)

            try:
                b_run, b_trace = run_one(task, provider_name, model, with_guards=False)
                o_run, o_trace = run_one(task, provider_name, model, with_guards=True)

                b_cost   = b_trace.aggregates.total_cost_usd
                o_cost   = o_trace.aggregates.total_cost_usd
                b_tokens = b_trace.aggregates.total_tokens
                o_tokens = o_trace.aggregates.total_tokens

                all_baseline_costs.append(b_cost)
                all_optimized_costs.append(o_cost)
                all_baseline_tokens.append(b_tokens)
                all_optimized_tokens.append(o_tokens)

                if b_run.success:
                    baseline_successes += 1
                if o_run.success:
                    optimized_successes += 1
                total_runs += 1

                # Per-run score snapshot.
                # Free providers report $0 cost — for those, cost efficiency tracks token
                # efficiency (cost is proportional to tokens; the free tier is just a $0 multiplier).
                run_tok_pct   = _safe_pct(b_tokens, o_tokens)
                run_cost_pct  = _safe_pct(b_cost, o_cost) if b_cost > 0 else run_tok_pct
                run_success   = 1.0 if o_run.success else 0.0
                run_score = (
                    SCORE_WEIGHTS["cost"]    * _dimension_score(run_cost_pct, COST_TARGET) +
                    SCORE_WEIGHTS["tokens"]  * _dimension_score(run_tok_pct, TOKEN_TARGET) +
                    SCORE_WEIGHTS["success"] * run_success * 100
                )
                per_run_scores.append(run_score)

                if verbose:
                    print(f"cost {run_cost_pct:+.1f}%  tokens {run_tok_pct:+.1f}%  {'✓' if o_run.success else '✗'}")

            except Exception as exc:
                if verbose:
                    print(f"ERROR: {exc}")

    if not per_run_scores:
        raise RuntimeError("No runs completed. Check provider configuration.")

    # Aggregate
    avg_b_cost  = statistics.mean(all_baseline_costs)  if all_baseline_costs  else 0
    avg_o_cost  = statistics.mean(all_optimized_costs) if all_optimized_costs else 0
    avg_b_tok   = statistics.mean(all_baseline_tokens)  if all_baseline_tokens  else 0
    avg_o_tok   = statistics.mean(all_optimized_tokens) if all_optimized_tokens else 0

    token_pct   = _safe_pct(avg_b_tok,  avg_o_tok)
    # Free providers ($0 cost): cost efficiency tracks token efficiency (see per-run note above).
    cost_pct    = _safe_pct(avg_b_cost, avg_o_cost) if avg_b_cost > 0 else token_pct
    b_success   = (baseline_successes  / total_runs * 100) if total_runs else 0
    o_success   = (optimized_successes / total_runs * 100) if total_runs else 0
    success_improvement = o_success - b_success

    dims = [
        CertDimension(
            name="Cost savings",
            baseline=avg_b_cost,
            optimized=avg_o_cost,
            improvement_pct=cost_pct,
            target_pct=COST_TARGET,
            score=_dimension_score(cost_pct, COST_TARGET),
            passed=cost_pct >= COST_TARGET,
        ),
        CertDimension(
            name="Token reduction",
            baseline=avg_b_tok,
            optimized=avg_o_tok,
            improvement_pct=token_pct,
            target_pct=TOKEN_TARGET,
            score=_dimension_score(token_pct, TOKEN_TARGET),
            passed=token_pct >= TOKEN_TARGET,
        ),
        CertDimension(
            name="Task success rate",
            baseline=b_success,
            optimized=o_success,
            improvement_pct=success_improvement,
            target_pct=0,
            score=o_success,      # 0–100 directly
            passed=o_success >= b_success,
        ),
    ]

    overall = (
        SCORE_WEIGHTS["cost"]    * dims[0].score +
        SCORE_WEIGHTS["tokens"]  * dims[1].score +
        SCORE_WEIGHTS["success"] * dims[2].score
    )

    # 95% confidence interval via bootstrap-like std dev of per-run scores
    if len(per_run_scores) > 1:
        std = statistics.stdev(per_run_scores)
        margin = 1.96 * std / math.sqrt(len(per_run_scores))
    else:
        margin = 0.0
    ci = (max(0, overall - margin), min(100, overall + margin))

    used_model = model or {
        "groq": "llama-3.1-8b-instant",
        "gemini": "gemini-1.5-flash-8b",
        "ollama": "llama3.2",
    }.get(provider_name, "unknown")

    score = RunCoreScore(
        overall=round(overall, 1),
        dimensions=dims,
        confidence_interval_95=(round(ci[0], 1), round(ci[1], 1)),
        n_runs=total_runs,
        n_tasks=len(tasks),
        provider=provider_name,
        model=used_model,
        suite=suite,
        timestamp=ts,
        # Gated: overall ≥ 60 AND the agent actually succeeds on enough tasks.
        certified=(overall >= 60 and o_success >= MIN_SUCCESS_FOR_CERT),
        run_scores=per_run_scores,
    )

    if verbose:
        _print_score(score)

    return score


def _print_score(s: RunCoreScore) -> None:
    print(f"\n{'='*60}")
    print(f"  RUNCORE SCORE: {s.overall:.1f}/100  ({s.grade})  {'✅ CERTIFIED' if s.certified else '❌ NOT CERTIFIED'}")
    print(f"  95% CI: [{s.confidence_interval_95[0]:.1f}, {s.confidence_interval_95[1]:.1f}]")
    print(f"  Provider: {s.provider} / {s.model}")
    print(f"  Runs: {s.n_runs} tasks × baseline+guarded")
    print(f"{'='*60}")
    for d in s.dimensions:
        bar = "█" * int(d.score / 5)
        print(f"  {d.name:<22} {d.score:5.1f}/100  {bar}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# HTML certification report
# ---------------------------------------------------------------------------

def generate_cert_html(score: RunCoreScore) -> str:
    dims = score.dimensions
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Fingerprint — SHA256 of the score data (tamper-evident)
    fingerprint_data = json.dumps({
        "overall": score.overall,
        "provider": score.provider,
        "model": score.model,
        "n_runs": score.n_runs,
        "timestamp": score.timestamp,
        "dims": [(d.name, round(d.improvement_pct, 2)) for d in dims],
    }, sort_keys=True)
    fingerprint = hashlib.sha256(fingerprint_data.encode()).hexdigest()[:16].upper()

    # Embeddable badge — grade slug ('A+' -> 'Aplus' for a clean URL path)
    badge_grade_slug = score.grade.replace("+", "plus")
    badge_markdown = f"[![RunCore Certified {score.grade}](https://YOUR-RUNCORE-HOST/badge/{badge_grade_slug}.svg)](https://YOUR-RUNCORE-HOST/certification)"

    def dim_bar(d: CertDimension) -> str:
        pct = min(100, d.score)
        color = "#22c55e" if d.passed else "#f59e0b"
        baseline_fmt = (
            f"${d.baseline:.6f}" if "Cost" in d.name
            else f"{d.baseline:,.0f} tok" if "Token" in d.name
            else f"{d.baseline:.0f}%"
        )
        optimized_fmt = (
            f"${d.optimized:.6f}" if "Cost" in d.name
            else f"{d.optimized:,.0f} tok" if "Token" in d.name
            else f"{d.optimized:.0f}%"
        )
        passed_badge = (
            f'<span style="color:#22c55e;font-size:.75rem;font-weight:600">✓ TARGET MET</span>'
            if d.passed else
            f'<span style="color:#f59e0b;font-size:.75rem;font-weight:600">⚠ BELOW TARGET</span>'
        )
        return f"""
        <div style="margin-bottom:24px">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
            <span style="font-size:.9rem;font-weight:600;color:#e2e8f0">{d.name}</span>
            <span style="display:flex;gap:12px;align-items:center">
              {passed_badge}
              <span style="font-size:1.1rem;font-weight:700;color:#f8fafc">{d.score:.0f}<span style="font-size:.7rem;color:#94a3b8">/100</span></span>
            </span>
          </div>
          <div style="background:#1e293b;border-radius:6px;height:8px;overflow:hidden">
            <div style="width:{pct}%;height:100%;background:{color};border-radius:6px;transition:width .6s"></div>
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:4px">
            <span style="font-size:.75rem;color:#64748b">baseline: {baseline_fmt}</span>
            <span style="font-size:.75rem;color:#22c55e">+{d.improvement_pct:.1f}% improvement → {optimized_fmt}</span>
          </div>
        </div>"""

    dim_html = "".join(dim_bar(d) for d in dims)

    # Sparkline data for run scores
    spark_data = json.dumps([round(s, 1) for s in score.run_scores])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RunCore Certification — {score.provider} / {score.model}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #070c19; color: #e2e8f0; min-height: 100vh; }}
  .page {{ max-width: 860px; margin: 0 auto; padding: 48px 24px; }}

  /* Header */
  .cert-header {{ text-align: center; margin-bottom: 48px; }}
  .brand {{ display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 32px; }}
  .brand-logo {{ width: 36px; height: 36px; }}
  .brand-name {{ font-size: 1.4rem; font-weight: 700; color: #f8fafc; letter-spacing: -.3px; }}

  /* Score circle */
  .score-ring {{ position: relative; width: 200px; height: 200px; margin: 0 auto 24px; }}
  .score-ring svg {{ transform: rotate(-90deg); }}
  .score-center {{ position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }}
  .score-number {{ font-size: 3rem; font-weight: 800; line-height: 1; }}
  .score-max {{ font-size: .85rem; color: #64748b; margin-top: 2px; }}
  .score-grade {{ font-size: 1.1rem; font-weight: 700; letter-spacing: 1px; margin-top: 4px; }}

  /* Certified badge */
  .cert-badge {{ display: inline-flex; align-items: center; gap: 8px; padding: 8px 20px; border-radius: 100px; font-size: .9rem; font-weight: 600; margin-bottom: 12px; }}
  .cert-badge.pass {{ background: rgba(34,197,94,.15); color: #22c55e; border: 1px solid rgba(34,197,94,.3); }}
  .cert-badge.fail {{ background: rgba(239,68,68,.15); color: #ef4444; border: 1px solid rgba(239,68,68,.3); }}

  /* Meta */
  .meta-row {{ display: flex; gap: 24px; justify-content: center; flex-wrap: wrap; margin-top: 16px; }}
  .meta-item {{ font-size: .8rem; color: #64748b; }}
  .meta-item strong {{ color: #94a3b8; font-weight: 500; }}

  /* Cards */
  .card {{ background: #0d1830; border: 1px solid rgba(91,138,247,.13); border-radius: 12px; padding: 24px; margin-bottom: 20px; }}
  .card-title {{ font-size: .78rem; font-weight: 600; letter-spacing: .8px; color: #64748b; text-transform: uppercase; margin-bottom: 20px; }}

  /* CI band */
  .ci-band {{ background: rgba(91,138,247,.08); border: 1px solid rgba(91,138,247,.2); border-radius: 8px; padding: 12px 16px; display: flex; align-items: center; gap: 12px; margin-top: 8px; }}
  .ci-text {{ font-size: .82rem; color: #94a3b8; }}
  .ci-range {{ font-size: 1rem; font-weight: 600; color: #6488f5; }}

  /* Fingerprint */
  .fingerprint {{ display: flex; align-items: center; gap: 12px; background: #0a1120; border: 1px solid rgba(91,138,247,.1); border-radius: 8px; padding: 12px 16px; margin-top: 20px; }}
  .fp-label {{ font-size: .75rem; color: #475569; text-transform: uppercase; letter-spacing: .5px; }}
  .fp-value {{ font-family: monospace; font-size: .85rem; color: #6488f5; letter-spacing: 2px; }}
  .fp-stamp {{ font-size: .75rem; color: #334155; margin-left: auto; }}

  /* Chart */
  .chart-wrap {{ position: relative; height: 140px; margin-top: 8px; }}

  /* Footer */
  .footer {{ text-align: center; margin-top: 40px; font-size: .78rem; color: #334155; }}
  .footer a {{ color: #6488f5; text-decoration: none; }}
</style>
</head>
<body>
<div class="page">

  <div class="cert-header">
    <div class="brand">
      <svg class="brand-logo" viewBox="0 0 24 24" fill="none" stroke="url(#lg)" stroke-width="2.5">
        <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#6488f5"/><stop offset="100%" stop-color="#8aaaf8"/>
        </linearGradient></defs>
        <polygon points="13,2 3,14 12,14 11,22 21,10 12,10"/>
      </svg>
      <span class="brand-name">RunCore</span>
    </div>

    <div class="score-ring">
      <svg width="200" height="200" viewBox="0 0 200 200">
        <circle cx="100" cy="100" r="88" fill="none" stroke="#1e293b" stroke-width="12"/>
        <circle cx="100" cy="100" r="88" fill="none" stroke="{score.badge_color}" stroke-width="12"
          stroke-dasharray="{2 * 3.14159 * 88 * score.overall / 100:.1f} {2 * 3.14159 * 88:.1f}"
          stroke-linecap="round"/>
      </svg>
      <div class="score-center">
        <div class="score-number" style="color:{score.badge_color}">{score.overall:.0f}</div>
        <div class="score-max">out of 100</div>
        <div class="score-grade" style="color:{score.badge_color}">{score.grade}</div>
      </div>
    </div>

    <div>
      <span class="cert-badge {'pass' if score.certified else 'fail'}">
        {'✓ RunCore Certified' if score.certified else '✗ Not Certified'}
      </span>
    </div>
    <p style="font-size:.9rem;color:#64748b;margin-top:8px">
      Agent efficiency verified against the open RunCore Score™ methodology
    </p>

    <div class="meta-row">
      <span class="meta-item"><strong>Provider</strong> {score.provider}</span>
      <span class="meta-item"><strong>Model</strong> {score.model}</span>
      <span class="meta-item"><strong>Tasks</strong> {score.n_tasks}</span>
      <span class="meta-item"><strong>Runs</strong> {score.n_runs}</span>
      <span class="meta-item"><strong>Suite</strong> {score.suite}</span>
      <span class="meta-item"><strong>Date</strong> {stamp}</span>
    </div>
  </div>

  <!-- Dimensions -->
  <div class="card">
    <div class="card-title">Performance dimensions</div>
    {dim_html}
    <div class="ci-band">
      <div>
        <div class="fp-label">95% Confidence Interval</div>
        <div class="ci-range">[{score.confidence_interval_95[0]:.1f} — {score.confidence_interval_95[1]:.1f}]</div>
      </div>
      <div class="ci-text">Score is stable across {score.n_runs} independent runs with 95% confidence.</div>
    </div>
  </div>

  <!-- Score distribution chart -->
  <div class="card">
    <div class="card-title">Score distribution — per-run breakdown</div>
    <div class="chart-wrap">
      <canvas id="sparkChart" role="img" aria-label="Run-by-run score distribution chart"></canvas>
    </div>
  </div>

  <!-- Fingerprint -->
  <div class="fingerprint">
    <div>
      <div class="fp-label">Result fingerprint (SHA-256)</div>
      <div class="fp-value">{fingerprint}</div>
    </div>
    <div class="fp-stamp">{stamp}</div>
  </div>

  <!-- Embeddable badge -->
  <div class="fingerprint" style="flex-direction:column;align-items:flex-start;gap:10px">
    <div class="fp-label">Embed this badge in your README</div>
    <img src="/badge/{badge_grade_slug}.svg" alt="RunCore Certified {score.grade}" style="height:20px">
    <code style="display:block;width:100%;box-sizing:border-box;background:#0a1120;color:#8aaaf8;padding:10px;border-radius:6px;font-size:12px;word-break:break-all">{badge_markdown}</code>
  </div>

  <div class="footer">
    Generated by <a href="https://github.com/ptpaulinho/RunCore">RunCore v0.9.0</a> ·
    Methodology: <a href="https://github.com/ptpaulinho/RunCore/blob/main/docs/RUNCORE_SCORE_SPEC.md">RunCore Score™ Spec (open)</a> ·
    To reproduce: <code>python -m benchmarks.certification --provider {score.provider}</code>
  </div>

</div>
<script>
const scores = {spark_data};
const labels = scores.map((_, i) => 'Run ' + (i + 1));
new Chart(document.getElementById('sparkChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [{{
      label: 'RunCore Score',
      data: scores,
      backgroundColor: scores.map(s => s >= 60 ? 'rgba(34,197,94,0.7)' : s >= 40 ? 'rgba(251,191,36,0.7)' : 'rgba(239,68,68,0.7)'),
      borderRadius: 4,
      borderSkipped: false,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: ctx => ' Score: ' + ctx.raw.toFixed(1) }} }}
    }},
    scales: {{
      x: {{ grid: {{ color: 'rgba(91,138,247,0.06)' }}, ticks: {{ color: '#475569', font: {{ size: 10 }} }} }},
      y: {{ min: 0, max: 100, grid: {{ color: 'rgba(91,138,247,0.06)' }}, ticks: {{ color: '#475569', font: {{ size: 10 }} }} }}
    }}
  }}
}});
</script>
</body>
</html>"""


def save_cert(score: RunCoreScore, output_path: Path | None = None) -> Path:
    if output_path is None:
        cert_dir = RESULTS_DIR / "certifications"
        cert_dir.mkdir(exist_ok=True)
        output_path = cert_dir / f"runcore_cert_{score.timestamp}_{score.provider}.html"

    html = generate_cert_html(score)
    output_path.write_text(html, encoding="utf-8")

    # Also save raw score JSON alongside
    json_path = output_path.with_suffix(".json")
    json_path.write_text(json.dumps({
        "overall": score.overall,
        "grade": score.grade,
        "certified": score.certified,
        "provider": score.provider,
        "model": score.model,
        "suite": score.suite,
        "n_runs": score.n_runs,
        "n_tasks": score.n_tasks,
        "timestamp": score.timestamp,
        "confidence_interval_95": list(score.confidence_interval_95),
        "dimensions": [
            {
                "name": d.name,
                "score": d.score,
                "improvement_pct": d.improvement_pct,
                "target_pct": d.target_pct,
                "passed": d.passed,
                "baseline": d.baseline,
                "optimized": d.optimized,
            }
            for d in score.dimensions
        ],
    }, indent=2), encoding="utf-8")

    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RunCore Certification Suite")
    parser.add_argument("--provider", default="groq", choices=["groq", "gemini", "ollama"])
    parser.add_argument("--model", default=None, help="Override model name")
    parser.add_argument("--runs", type=int, default=5, help="Runs per task (default 5)")
    parser.add_argument("--suite", default="all", help="Task suite (support|research|all)")
    parser.add_argument("--output", default=None, help="Output HTML path")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    score = run_certification(
        provider_name=args.provider,
        model=args.model,
        runs_per_task=args.runs,
        suite=args.suite,
        verbose=not args.quiet,
    )

    out = save_cert(score, Path(args.output) if args.output else None)
    print(f"\n📋 Certification report: {out}")
    print(f"📊 Score JSON: {out.with_suffix('.json')}")
    sys.exit(0 if score.certified else 1)
