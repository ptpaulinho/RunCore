"""Tests for the RunCore FastAPI server endpoints."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from runcore.server.app import app, _runs, _lock

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_atir_trace(agent_name: str = "test_agent") -> dict:
    """Minimal valid ATIR trace dict for POST /advice."""
    return {
        "atir_version": "1.0",
        "trace_id": str(uuid.uuid4()),
        "agent_name": agent_name,
        "task": "test task",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "success": True,
        "quality_score": 0.85,
        "provider": "anthropic",
        "framework": "runcore",
        "spans": [
            {
                "type": "llm_call",
                "span_id": str(uuid.uuid4()),
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": 400.0,
                "input_tokens": 500,
                "output_tokens": 100,
                "cost_usd": 0.0002,
                "metadata": {},
            },
            {
                "type": "tool_call",
                "span_id": str(uuid.uuid4()),
                "name": "get_invoice",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": 15.0,
                "input_tokens": 50,
                "success": True,
                "arguments": {"invoice_id": "INV-001"},
                "metadata": {},
            },
        ],
        "tags": [],
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_dashboard_returns_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "RunCore" in resp.text


def test_dashboard_contains_benchmark_form():
    resp = client.get("/")
    assert resp.status_code == 200
    # Dashboard should have a benchmark trigger element
    assert "benchmark" in resp.text.lower()


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------

def test_status_endpoint():
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_runs" in data
    assert "running" in data
    assert "done" in data
    assert isinstance(data["total_runs"], int)


# ---------------------------------------------------------------------------
# POST /benchmark
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_benchmark_returns_run_id():
    resp = client.post("/benchmark", json={
        "agent": "support",
        "tasks": ["Refund invoice #1001"],
        "runs_per_task": 1,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert data["status"] == "queued"
    assert isinstance(data["run_id"], str)


@pytest.mark.slow
def test_benchmark_run_id_registered():
    resp = client.post("/benchmark", json={
        "agent": "support",
        "tasks": ["Test task"],
        "runs_per_task": 1,
    })
    run_id = resp.json()["run_id"]
    with _lock:
        assert run_id in _runs


@pytest.mark.slow
def test_benchmark_default_agent():
    resp = client.post("/benchmark", json={
        "tasks": ["some task"],
        "runs_per_task": 1,
    })
    assert resp.status_code == 200
    assert "run_id" in resp.json()


# ---------------------------------------------------------------------------
# GET /reports
# ---------------------------------------------------------------------------

def test_list_reports_returns_list():
    resp = client.get("/reports")
    assert resp.status_code == 200
    data = resp.json()
    assert "reports" in data
    assert isinstance(data["reports"], list)


# ---------------------------------------------------------------------------
# GET /reports/{run_id} — 404 for unknown
# ---------------------------------------------------------------------------

def test_report_404_for_unknown():
    resp = client.get(f"/reports/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_report_json_404_for_unknown():
    resp = client.get(f"/reports/{uuid.uuid4()}/json")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/advice — 404 for unknown
# ---------------------------------------------------------------------------

def test_advice_404_for_unknown_run():
    resp = client.get(f"/runs/{uuid.uuid4()}/advice")
    assert resp.status_code == 404


def test_advice_from_memory():
    run_id = str(uuid.uuid4())
    fake_advice = {"prescriptions": [], "total_estimated_savings_pct": 0.0}
    with _lock:
        _runs[run_id] = {"status": "done", "advice": fake_advice}
    resp = client.get(f"/runs/{run_id}/advice")
    assert resp.status_code == 200
    data = resp.json()
    assert "prescriptions" in data


# ---------------------------------------------------------------------------
# POST /advice
# ---------------------------------------------------------------------------

def test_advice_endpoint_with_valid_traces():
    trace = _make_atir_trace()
    resp = client.post("/advice", json={"traces": [trace], "agent_name": "test_agent"})
    assert resp.status_code == 200
    data = resp.json()
    assert "prescriptions" in data
    assert isinstance(data["prescriptions"], list)
    assert "total_estimated_savings_pct" in data


def test_advice_endpoint_empty_traces():
    resp = client.post("/advice", json={"traces": []})
    assert resp.status_code == 400


def test_advice_endpoint_multiple_traces():
    traces = [_make_atir_trace() for _ in range(5)]
    resp = client.post("/advice", json={"traces": traces})
    assert resp.status_code == 200
    data = resp.json()
    assert "prescriptions" in data


def test_advice_endpoint_invalid_trace():
    resp = client.post("/advice", json={"traces": [{"bad": "trace"}]})
    assert resp.status_code == 422


def test_advice_endpoint_without_agent_name():
    trace = _make_atir_trace()
    resp = client.post("/advice", json={"traces": [trace]})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /compare
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_compare_endpoint_returns_comparison():
    resp = client.post("/compare", json={
        "agent": "support",
        "tasks": ["Refund invoice #1001"],
        "runs_per_task": 1,
        "config_a": {},
        "config_b": {"enable_context_compression": False},
        "label_a": "With compression",
        "label_b": "Without compression",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "label_a" in data or "winner" in data or "config_a" in data or "result_a" in data


@pytest.mark.slow
def test_compare_endpoint_default_configs():
    resp = client.post("/compare", json={
        "agent": "support",
        "tasks": ["test task"],
        "runs_per_task": 1,
    })
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/stream — basic connectivity
# ---------------------------------------------------------------------------

def test_stream_unknown_run_returns_sse():
    run_id = str(uuid.uuid4())
    # Even for unknown runs, SSE endpoint should connect and send initial state
    with client.stream("GET", f"/runs/{run_id}/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        # Read first chunk
        for chunk in resp.iter_text():
            assert "event:" in chunk or ":" in chunk  # SSE format or keep-alive
            break


def test_stream_known_done_run():
    run_id = str(uuid.uuid4())
    with _lock:
        _runs[run_id] = {"status": "done", "pct": 100}
    with client.stream("GET", f"/runs/{run_id}/stream") as resp:
        assert resp.status_code == 200
        for chunk in resp.iter_text():
            assert len(chunk) > 0
            break
