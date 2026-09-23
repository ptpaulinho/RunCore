# Cookbook 4 — Cloud auto-push

Send every trace to RunCore Cloud automatically.

## Setup

```bash
# 1. Start your RunCore Cloud instance (or deploy on Render)
#    See render.yaml in the repo root for one-click deploy

# 2. Create a tenant
curl -X POST https://your-runcore.onrender.com/cloud/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "My Company", "plan": "free"}'
# → {"id": "...", "api_key": "rc_...", "plan": "free"}
```

## Configure SDK

```python
import runcore

# Call once at app startup (or put in settings.py / config.py)
runcore.configure(
    api_key="rc_...",
    endpoint="https://your-runcore.onrender.com",
    on_error="warn",   # "warn" | "raise" | "silent"
)
```

## Use normally — traces push automatically

```python
# Every capture() now pushes to Cloud in background (daemon thread)
with runcore.capture("support_agent", task="process refund") as cap:
    # ... your agent code ...
    pass

# Trace was pushed — check your Cloud dashboard
# https://your-runcore.onrender.com/cloud/dashboard
# (pass API key in Authorization: Bearer header)
```

## Check push stats

```python
from runcore.sdk.cloud import push_stats
stats = push_stats()
print(f"Pushed: {stats['pushed']}")
print(f"Errors: {stats['errors']}")
```

## Use environment variables instead

```bash
export RUNCORE_API_KEY="rc_..."
export RUNCORE_CLOUD_ENDPOINT="https://your-runcore.onrender.com"
```

```python
# runcore.configure() is NOT needed when env vars are set
import runcore
# auto_push is True if RUNCORE_API_KEY is set
```

## Dashboard

```bash
# View your traces
curl https://your-runcore.onrender.com/cloud/dashboard \
  -H "Authorization: Bearer rc_..."
```

Or open in browser — you get:
- KPI cards (total traces, success rate, avg CpST, total spend)
- Recent traces table (agent, cost, tokens, CpST)
- Quick-start code snippet

## Tier limits

| Plan | Traces/month | Price |
|------|-------------|-------|
| Free | 500 | $0 |
| Team | 10,000 | $49/mo |
| Enterprise | Unlimited | $299/mo |

Upgrade via `POST /cloud/billing/checkout`:
```python
import requests
resp = requests.post(
    "https://your-runcore.onrender.com/cloud/billing/checkout",
    json={"plan": "team"},
    headers={"Authorization": "Bearer rc_..."},
)
print(resp.json()["url"])  # Redirect user here for Stripe checkout
```
