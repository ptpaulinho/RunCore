"""Tests for RunCore billing tiers, limits, and billing endpoints."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Isolated DB per test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_billing.db"
    monkeypatch.setattr("runcore.server.storage._DB_PATH", db_path)
    monkeypatch.setattr("runcore.server.storage._POSTGRES", False)
    from runcore.server import storage
    storage.init_db()
    yield db_path


@pytest.fixture()
def client(tmp_path):
    with patch("runcore.server.app._REPORTS_DIR", tmp_path / "reports"):
        (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
        from runcore.server.app import app
        with TestClient(app) as c:
            yield c


@pytest.fixture()
def free_tenant(client):
    resp = client.post("/cloud/tenants", json={"name": "Free Corp", "plan": "free"})
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture()
def team_tenant(client):
    resp = client.post("/cloud/tenants", json={"name": "Team Corp", "plan": "team"})
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture()
def enterprise_tenant(client):
    resp = client.post("/cloud/tenants", json={"name": "Enterprise Corp", "plan": "enterprise"})
    assert resp.status_code == 201
    return resp.json()


def _headers(tenant):
    return {"Authorization": f"Bearer {tenant['api_key']}"}


def _trace(trace_id="t-001"):
    return {
        "atir_version": "1.0",
        "trace_id": trace_id,
        "agent_name": "bot",
        "task": "test",
        "framework": "test",
        "started_at": "2026-06-17T10:00:00Z",
        "finished_at": "2026-06-17T10:00:01Z",
        "success": True,
        "quality_score": 1.0,
        "provider": "openai",
        "spans": [],
        "aggregates": {
            "total_cost_usd": 0.001,
            "total_tokens": 100,
            "llm_calls": 1,
            "tool_calls": 1,
            "cost_per_successful_task": 0.001,
            "loop_risk_score": 0.0,
            "success_rate": 1.0,
            "avg_quality_score": 1.0,
        },
        "tags": {},
        "metadata": {},
    }


# ===========================================================================
# Tier model tests (pure logic, no HTTP)
# ===========================================================================

class TestTierLimits:
    def test_free_limits(self):
        from runcore.server.billing import get_limits
        lim = get_limits("free")
        assert lim.traces_per_month == 500
        assert lim.retention_days == 7
        assert lim.price_usd_month == 0.0

    def test_team_limits(self):
        from runcore.server.billing import get_limits
        lim = get_limits("team")
        assert lim.traces_per_month == 10_000
        assert lim.retention_days == 30
        assert lim.price_usd_month == 49.0

    def test_enterprise_unlimited(self):
        from runcore.server.billing import get_limits
        lim = get_limits("enterprise")
        assert lim.traces_per_month == -1  # unlimited

    def test_unknown_plan_falls_back_to_free(self):
        from runcore.server.billing import get_limits
        lim = get_limits("nonexistent")
        assert lim.plan == "free"

    def test_check_ingest_allowed_under_limit(self):
        from runcore.server.billing import check_ingest_allowed
        allowed, reason = check_ingest_allowed("free", 100, 1)
        assert allowed
        assert reason == ""

    def test_check_ingest_allowed_at_limit(self):
        from runcore.server.billing import check_ingest_allowed
        allowed, reason = check_ingest_allowed("free", 500, 1)
        assert not allowed
        assert "500" in reason

    def test_check_ingest_batch_exceeds_remaining(self):
        from runcore.server.billing import check_ingest_allowed
        allowed, reason = check_ingest_allowed("free", 498, 5)  # 2 remaining, batch=5
        assert not allowed
        assert "2" in reason

    def test_enterprise_always_allowed(self):
        from runcore.server.billing import check_ingest_allowed
        allowed, _ = check_ingest_allowed("enterprise", 9_999_999, 1000)
        assert allowed

    def test_has_feature_free(self):
        from runcore.server.billing import has_feature
        assert has_feature("free", "basic_dashboard")
        assert not has_feature("free", "advisor")

    def test_has_feature_team(self):
        from runcore.server.billing import has_feature
        assert has_feature("team", "advisor")
        assert not has_feature("team", "sso")

    def test_has_feature_enterprise(self):
        from runcore.server.billing import has_feature
        assert has_feature("enterprise", "sso")
        assert has_feature("enterprise", "audit_log")

    def test_tier_comparison_has_all_plans(self):
        from runcore.server.billing import TIER_COMPARISON
        plans = [t["plan"] for t in TIER_COMPARISON]
        assert "free" in plans
        assert "team" in plans
        assert "enterprise" in plans


# ===========================================================================
# Storage billing fields
# ===========================================================================

class TestStorageBilling:
    def test_upgrade_tenant_plan(self):
        from runcore.server import storage
        t = storage.create_tenant("Upgrade Co", "free")
        storage.upgrade_tenant_plan(t["id"], "team", "cus_abc123")
        updated = storage.get_tenant_by_id(t["id"])
        assert updated["plan"] == "team"
        assert updated["stripe_customer_id"] == "cus_abc123"

    def test_downgrade_by_customer_id(self):
        from runcore.server import storage
        t = storage.create_tenant("Downgrade Co", "team")
        storage.upgrade_tenant_plan(t["id"], "team", "cus_xyz")
        storage.downgrade_tenant_by_customer("cus_xyz", plan="free")
        updated = storage.get_tenant_by_id(t["id"])
        assert updated["plan"] == "free"

    def test_monthly_usage_counter(self):
        from runcore.server import storage
        t = storage.create_tenant("Counter Co")
        assert storage.get_monthly_usage(t["id"]) == 0
        for i in range(3):
            tr = _trace(f"u-{i}")
            storage.ingest_trace(t["id"], tr)
        assert storage.get_monthly_usage(t["id"]) == 3

    def test_monthly_usage_in_stats(self):
        from runcore.server import storage
        t = storage.create_tenant("Stats2 Co")
        storage.ingest_trace(t["id"], _trace("s-001"))
        assert storage.get_monthly_usage(t["id"]) == 1


# ===========================================================================
# Ingest tier enforcement (HTTP)
# ===========================================================================

class TestIngestTierEnforcement:
    def test_free_ingest_within_limit(self, client, free_tenant):
        resp = client.post(
            "/cloud/ingest",
            json={"traces": [_trace("ok-1")]},
            headers=_headers(free_tenant),
        )
        assert resp.status_code == 200
        d = resp.json()
        assert d["ingested"] == 1
        assert d["usage"]["plan"] == "free"
        assert d["usage"]["limit"] == 500

    def test_ingest_response_includes_usage(self, client, free_tenant):
        resp = client.post(
            "/cloud/ingest",
            json={"traces": [_trace("usage-1")]},
            headers=_headers(free_tenant),
        )
        assert resp.status_code == 200
        usage = resp.json()["usage"]
        assert "traces_this_month" in usage
        assert "limit" in usage

    def test_free_limit_enforced(self, client, free_tenant):
        """Simulate free tenant at 500 traces — next ingest should be 429."""
        from runcore.server import storage
        # Directly set counter to 500
        with storage._lock:
            con = storage._conn()
            con.execute(
                "UPDATE tenants SET traces_this_month=500 WHERE id=?",
                (free_tenant["id"],),
            )
            con.commit()
            con.close()

        resp = client.post(
            "/cloud/ingest",
            json={"traces": [_trace("over-limit")]},
            headers=_headers(free_tenant),
        )
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["error"] == "trace_limit_exceeded"
        assert "free" in detail["message"].lower() or "500" in detail["message"]

    def test_team_higher_limit(self, client, team_tenant):
        """Team plan allows up to 10,000 traces."""
        resp = client.post(
            "/cloud/ingest",
            json={"traces": [_trace("team-1")]},
            headers=_headers(team_tenant),
        )
        assert resp.status_code == 200
        assert resp.json()["usage"]["limit"] == 10_000

    def test_enterprise_no_limit(self, client, enterprise_tenant):
        """Enterprise plan has no trace limit."""
        resp = client.post(
            "/cloud/ingest",
            json={"traces": [_trace("ent-1")]},
            headers=_headers(enterprise_tenant),
        )
        assert resp.status_code == 200
        assert resp.json()["usage"]["limit"] == -1  # unlimited


# ===========================================================================
# Stats endpoint with billing info
# ===========================================================================

class TestStatsWithBilling:
    def test_stats_includes_plan(self, client, free_tenant):
        resp = client.get("/cloud/stats", headers=_headers(free_tenant))
        assert resp.status_code == 200
        d = resp.json()
        assert d["plan"] == "free"
        assert d["traces_limit"] == 500
        assert "traces_this_month" in d

    def test_stats_team_limit(self, client, team_tenant):
        resp = client.get("/cloud/stats", headers=_headers(team_tenant))
        assert resp.json()["traces_limit"] == 10_000


# ===========================================================================
# Billing endpoints
# ===========================================================================

class TestBillingEndpoints:
    def test_plans_page_200(self, client):
        resp = client.get("/cloud/billing/plans")
        assert resp.status_code == 200
        assert "Free" in resp.text
        assert "Team" in resp.text
        assert "Enterprise" in resp.text
        assert "$49" in resp.text
        assert "$299" in resp.text

    def test_checkout_dev_mode(self, client, free_tenant):
        """Without Stripe keys, checkout returns a dev URL."""
        resp = client.post(
            "/cloud/billing/checkout",
            json={"plan": "team"},
            headers=_headers(free_tenant),
        )
        assert resp.status_code == 200
        d = resp.json()
        assert d["dev_mode"] is True
        assert "url" in d

    def test_checkout_enterprise(self, client, free_tenant):
        resp = client.post(
            "/cloud/billing/checkout",
            json={"plan": "enterprise"},
            headers=_headers(free_tenant),
        )
        assert resp.status_code == 200
        assert resp.json()["dev_mode"] is True

    def test_checkout_invalid_plan(self, client, free_tenant):
        resp = client.post(
            "/cloud/billing/checkout",
            json={"plan": "diamond"},
            headers=_headers(free_tenant),
        )
        assert resp.status_code == 400

    def test_checkout_requires_auth(self, client):
        resp = client.post("/cloud/billing/checkout", json={"plan": "team"})
        assert resp.status_code == 401

    def test_portal_no_customer_400(self, client, free_tenant):
        """Portal fails if tenant has no Stripe customer ID."""
        resp = client.post("/cloud/billing/portal", headers=_headers(free_tenant))
        assert resp.status_code == 400

    def test_portal_with_customer_dev_mode(self, client, free_tenant):
        """Portal works when tenant has a Stripe customer ID (dev mode returns placeholder)."""
        from runcore.server import storage
        storage.upgrade_tenant_plan(free_tenant["id"], "team", stripe_customer_id="cus_test123")
        resp = client.post("/cloud/billing/portal", headers=_headers(free_tenant))
        assert resp.status_code == 200
        assert "url" in resp.json()

    def test_webhook_dev_mode(self, client):
        """In dev mode (no webhook secret) any valid JSON is accepted."""
        payload = json.dumps({
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": "t-1", "metadata": {"plan": "team"}}}
        })
        resp = client.post(
            "/cloud/billing/webhook",
            content=payload,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["received"] is True

    def test_webhook_invalid_payload(self, client, monkeypatch):
        """With a webhook secret configured, bad signature → 400."""
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        import runcore.server.stripe_billing as _sb
        monkeypatch.setattr(_sb, "_STRIPE_WEBHOOK_SECRET", "whsec_test")
        resp = client.post(
            "/cloud/billing/webhook",
            content=b"bad payload",
            headers={"stripe-signature": "invalid"},
        )
        assert resp.status_code == 400

    def test_dev_checkout_page(self, client):
        resp = client.get("/cloud/billing/dev-checkout?plan=team&tenant=abc123")
        assert resp.status_code == 200
        assert "Dev Mode" in resp.text


# ===========================================================================
# Stripe webhook event handling
# ===========================================================================

class TestStripeWebhookHandling:
    def test_checkout_completed_upgrades_tenant(self, client, free_tenant):
        from runcore.server import storage, stripe_billing
        payload = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": free_tenant["id"],
                    "customer": "cus_abc",
                    "subscription": "sub_abc",
                    "metadata": {"tenant_id": free_tenant["id"], "plan": "team"},
                }
            },
        }
        action = stripe_billing.handle_webhook_event(payload, storage)
        assert "team" in action
        updated = storage.get_tenant_by_id(free_tenant["id"])
        assert updated["plan"] == "team"

    def test_subscription_deleted_downgrades(self, client, team_tenant):
        from runcore.server import storage, stripe_billing
        storage.upgrade_tenant_plan(team_tenant["id"], "team", stripe_customer_id="cus_down")
        payload = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_down"}},
        }
        action = stripe_billing.handle_webhook_event(payload, storage)
        assert "free" in action
        updated = storage.get_tenant_by_id(team_tenant["id"])
        assert updated["plan"] == "free"

    def test_unknown_event_handled_gracefully(self):
        from runcore.server import storage, stripe_billing
        payload = {"type": "unknown.event.type", "data": {"object": {}}}
        action = stripe_billing.handle_webhook_event(payload, storage)
        assert "unhandled" in action
