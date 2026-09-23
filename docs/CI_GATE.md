# RunCore CI gate

Catch agent regressions before they ship. The gate runs your agent under
RunCore's guards, measures **cost/run** and **success rate**, and **fails the
build** when either regresses past your thresholds versus a committed baseline.

This is the core of RunCore's value: a test that protects you from quietly
shipping an agent that got more expensive or less reliable.

## 1. Record a baseline (once, on a known-good commit)

```bash
pip install "runcore[server]"
export GROQ_API_KEY=...            # or GEMINI_API_KEY / run Ollama
runcore ci --provider groq --suite support --runs 3 --update-baseline
git add .runcore/ci_baseline.json && git commit -m "chore: runcore baseline"
```

This writes `.runcore/ci_baseline.json` (cost/run, tokens/run, success rate).
Commit it — future runs compare against it.

## 2. Gate every PR

Locally:

```bash
runcore ci --provider groq --suite support --runs 3
# exit 0 = no regression; exit 1 = regression (build fails)
```

In GitHub Actions — copy [`docs/ci/example-workflow.yml`](ci/example-workflow.yml)
into your repo's `.github/workflows/`, and add `GROQ_API_KEY` as a repo secret.

```yaml
- uses: ptpaulinho/RunCore/.github/actions/runcore-gate@main
  with:
    provider: groq
    suite: support
    max-cost-increase: "10"   # fail if cost/run +10%
    max-success-drop: "5"     # fail if success drops 5 points
  env:
    GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
```

## Thresholds

| Flag | Default | Meaning |
|---|---|---|
| `--max-cost-increase` | 10 | fail if cost/run rises more than this % |
| `--max-success-drop` | 5 | fail if success rate drops more than this many points |
| `--min-success` | 0 | absolute success-rate floor (0–1; 0 = ignore) |

Free providers report $0 cost — the gate falls back to **tokens/run** for the
cost check automatically.

## Output

- Console: baseline-vs-current table + PASS/FAIL panel.
- `.runcore/ci_result.json`: machine-readable result (uploaded as a CI artifact
  by the action), e.g.

```json
{ "cost_increase_pct": 3.2, "success_drop_pp": 0.0, "passed": true, "failures": [] }
```

## Updating the baseline

When a cost/quality change is intentional, refresh the baseline and commit it:

```bash
runcore ci --provider groq --suite support --update-baseline
git commit -am "chore: refresh runcore baseline"
```

> **Bring-your-own-agent in CI:** today the gate runs the built-in benchmark
> suite against a provider (great for catching model/prompt/config regressions).
> To gate *your own* agent, wrap it with the SDK
> (`with runcore.capture(..., guards=GuardConfig()):`) and assert on
> `run.savings` / success in your own test — first-class `runcore ci --agent`
> support is on the roadmap.
