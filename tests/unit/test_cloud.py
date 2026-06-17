"""Tests for RunCore Cloud — multi-tenant ingest API, storage, and dashboard."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Redirect DB to a temp file for tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Each test gets its own isolated SQLite database."""
    db_path = tmp_path / "test_cloud.db"
    monkeypatch.setattr("runcore.server.storage._DB_PATH", db_path)
    # Reinitialise schema on the new path
    from runcore.server import storage
    storage.init_db()
    yield db_path


@pytest.fixture()
def client(tmp_path):
    # Also redirect reports dir
    with patch("runcore.server.app._REPORTS_DIR", tmp_path / "reports"):
        (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
        from runcore.server.app import app
        with TestClient(app) as c:
            yield c


@pytest.fixture()
def tenant(client):
    resp = client.post("/cloud/tenants", json={"name": "Acme Corp", "plan": "team"})
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture()
def auth_headers(tenant):
    return {"Authorization": f"Bearer {tenant['api_key']}"}


def _make_atir_trace(agent_name="test_agent", success=True):
    return {
        "atir_version": "1.0",
        "trace_id": "trace-abc-001",
        "agent_name": agent_name,
        "task": "classify email",
        "framework": "test",
        "started_at": "2026-06-17T10:00:00Z",
        "finished_at": "2026-06-17T10:00:01Z",
        "success": success,
        "quality_score": 0.95,
        "provider": "openai",
        "spans": [],
        "aggregates": {
            "total_cost_usd": 0.00025,
            "total_tokens": 350,
            "llm_calls": 1,
            "tool_calls": 2,
            "cost_per_successful_task": 0.00025,
            "loop_risk_score": 0.1,
            "success_rate": 1.0,
            "avg_quality_score": 0.95,
        },
        "tags": {},
        "metadata": {},
    }


# ===========================================================================
# Storage unit tests (no HTTP)
# ===========================================================================

class TestStorage:
    def test_create_and_get_tenant(self):
        from runcore.server import storage
        t = storage.create_tenant("Test Co", "free")
        assert t["api_key"].startswith("rc_")
        assert t["plan"] == "free"
        found = storage.get_tenant_by_key(t["api_key"])
        assert found["name"] == "Test Co"

    def test_invalid_key_returns_none(self):
        from runcore.server import storage
        assert storage.get_tenant_by_key("rc_invalid_key") is None

    def test_list_tenants(self):
        from runcore.server import storage
        storage.create_tenant("A", "free")
        storage.create_tenant("B", "team")
        tenants = storage.list_tenants()
        names = [t["name"] for t in tenants]
        assert "A" in names and "B" in names

    def test_ingest_and_get_trace(self):
        from runcore.server import storage
        t = storage.create_tenant("Trace Co")
        trace = _make_atir_trace()
        tid = storage.ingest_trace(t["id"], trace)
        assert tid == "trace-abc-001"
        retrieved = storage.get_trace(t["id"], tid)
        assert retrieved["agent_name"] == "test_agent"

    def test_ingest_cross_tenant_isolation(self):
        from runcore.server import storage
        t1 = storage.create_tenant("Tenant 1")
        t2 = storage.create_tenant("Tenant 2")
        trace = _make_atir_trace()
        storage.ingest_trace(t1["id"], trace)
        # t2 cannot see t1's trace
        result = storage.get_trace(t2["id"], "trace-abc-001")
        assert result is None

    def test_list_traces_pagination(self):
        from runcore.server import storage
        t = storage.create_tenant("Pager")
        for i in range(5):
            tr = _make_atir_trace()
            tr["trace_id"] = f"trace-{i:03d}"
            storage.ingest_trace(t["id"], tr)
        page1 = storage.list_traces(t["id"], limit=3, offset=0)
        page2 = storage.list_traces(t["id"], limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2

    def test_tenant_stats(self):
        from runcore.server import storage
        t = storage.create_tenant("Stats Co")
        for i in range(3):
            tr = _make_atir_trace(success=(i < 2))
            tr["trace_id"] = f"s-{i}"
            storage.ingest_trace(t["id"], tr)
        stats = storage.tenant_stats(t["id"])
        assert stats["total_traces"] == 3
        assert stats["successful"] == 2
        assert stats["success_rate"] == pytest.approx(66.7, abs=0.1)

    def test_ingest_upsert_same_trace_id(self):
        from runcore.server import storage
        t = storage.create_tenant("Upsert Co")
        tr = _make_atir_trace()
        storage.ingest_trace(t["id"], tr)
        tr["agent_name"] = "updated_agent"
        storage.ingest_trace(t["id"], tr)
        retrieved = storage.get_trace(t["id"], "trace-abc-001")
        assert retrieved["agent_name"] == "updated_agent"


# ===========================================================================
# HTTP API tests
# ===========================================================================

class TestTenantEndpoints:
    def test_create_tenant_201(self, client):
        resp = client.post("/cloud/tenants", json={"name": "New Corp"})
        assert resp.status_code == 201
        d = resp.json()
        assert d["api_key"].startswith("rc_")
        assert d["name"] == "New Corp"
        assert d["plan"] == "free"

    def test_create_tenant_with_plan(self, client):
        resp = client.post("/cloud/tenants", json={"name": "Big Corp", "plan": "enterprise"})
        assert resp.status_code == 201
        assert resp.json()["plan"] == "enterprise"

    def test_list_tenants(self, client, tenant):
        resp = client.get("/cloud/tenants")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["tenants"]]
        assert "Acme Corp" in names

    def test_list_tenants_no_api_keys_exposed(self, client, tenant):
        resp = client.get("/cloud/tenants")
        for t in resp.json()["tenants"]:
            assert "api_key" not in t


class TestIngestEndpoint:
    def test_ingest_one_trace(self, client, auth_headers):
        trace = _make_atir_trace()
        resp = client.post("/cloud/ingest", json={"traces": [trace]}, headers=auth_headers)
        assert resp.status_code == 200
        d = resp.json()
        assert d["ingested"] == 1
        assert len(d["trace_ids"]) == 1
        assert d["errors"] == []

    def test_ingest_multiple_traces(self, client, auth_headers):
        traces = []
        for i in range(3):
            tr = _make_atir_trace()
            tr["trace_id"] = f"multi-{i}"
            traces.append(tr)
        resp = client.post("/cloud/ingest", json={"traces": traces}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 3

    def test_ingest_requires_auth(self, client):
        resp = client.post("/cloud/ingest", json={"traces": [_make_atir_trace()]})
        assert resp.status_code == 401

    def test_ingest_invalid_key(self, client):
        resp = client.post(
            "/cloud/ingest",
            json={"traces": [_make_atir_trace()]},
            headers={"Authorization": "Bearer rc_invalid"},
        )
        assert resp.status_code == 401

    def test_ingest_empty_list_400(self, client, auth_headers):
        resp = client.post("/cloud/ingest", json={"traces": []}, headers=auth_headers)
        assert resp.status_code == 400

    def test_ingest_missing_bearer_401(self, client):
        resp = client.post(
            "/cloud/ingest",
            json={"traces": [_make_atir_trace()]},
            headers={"Authorization": "Token abc"},
        )
        assert resp.status_code == 401


class TestTraceEndpoints:
    def _ingest(self, client, auth_headers, trace_id="trace-xyz"):
        tr = _make_atir_trace()
        tr["trace_id"] = trace_id
        client.post("/cloud/ingest", json={"traces": [tr]}, headers=auth_headers)
        return trace_id

    def test_list_traces(self, client, auth_headers):
        self._ingest(client, auth_headers, "t-001")
        self._ingest(client, auth_headers, "t-002")
        resp = client.get("/cloud/traces", headers=auth_headers)
        assert resp.status_code == 200
        d = resp.json()
        assert d["count"] == 2

    def test_list_traces_requires_auth(self, client):
        resp = client.get("/cloud/traces")
        assert resp.status_code == 401

    def test_get_trace_by_id(self, client, auth_headers):
        tid = self._ingest(client, auth_headers, "single-trace")
        resp = client.get(f"/cloud/traces/{tid}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["agent_name"] == "test_agent"

    def test_get_trace_404(self, client, auth_headers):
        resp = client.get("/cloud/traces/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_cross_tenant_trace_isolation(self, client):
        # Create two tenants
        t1 = client.post("/cloud/tenants", json={"name": "T1"}).json()
        t2 = client.post("/cloud/tenants", json={"name": "T2"}).json()
        h1 = {"Authorization": f"Bearer {t1['api_key']}"}
        h2 = {"Authorization": f"Bearer {t2['api_key']}"}

        tr = _make_atir_trace()
        tr["trace_id"] = "shared-id"
        client.post("/cloud/ingest", json={"traces": [tr]}, headers=h1)

        # T2 cannot see T1's trace
        resp = client.get("/cloud/traces/shared-id", headers=h2)
        assert resp.status_code == 404

    def test_list_traces_pagination(self, client, auth_headers):
        for i in range(5):
            tr = _make_atir_trace()
            tr["trace_id"] = f"page-{i}"
            client.post("/cloud/ingest", json={"traces": [tr]}, headers=auth_headers)
        resp = client.get("/cloud/traces?limit=3&offset=0", headers=auth_headers)
        assert len(resp.json()["traces"]) == 3


class TestDashboardEndpoint:
    def test_dashboard_200(self, client, auth_headers):
        resp = client.get("/cloud/dashboard", headers=auth_headers)
        assert resp.status_code == 200
        assert "RunCore Cloud" in resp.text
        assert "Acme Corp" in resp.text

    def test_dashboard_requires_auth(self, client):
        resp = client.get("/cloud/dashboard")
        assert resp.status_code == 401

    def test_dashboard_shows_traces(self, client, auth_headers):
        tr = _make_atir_trace("my_pipeline")
        client.post("/cloud/ingest", json={"traces": [tr]}, headers=auth_headers)
        resp = client.get("/cloud/dashboard", headers=auth_headers)
        assert "my_pipeline" in resp.text


class TestStatsEndpoint:
    def test_stats_empty(self, client, auth_headers):
        resp = client.get("/cloud/stats", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total_traces"] == 0

    def test_stats_after_ingest(self, client, auth_headers):
        for i in range(4):
            tr = _make_atir_trace(success=(i < 3))
            tr["trace_id"] = f"s-{i}"
            client.post("/cloud/ingest", json={"traces": [tr]}, headers=auth_headers)
        resp = client.get("/cloud/stats", headers=auth_headers)
        d = resp.json()
        assert d["total_traces"] == 4
        assert d["successful"] == 3
        assert d["success_rate"] == pytest.approx(75.0, abs=0.1)

    def test_stats_requires_auth(self, client):
        resp = client.get("/cloud/stats")
        assert resp.status_code == 401
