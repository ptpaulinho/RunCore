"""Tenant-isolated trace storage for RunCore Cloud.

Uses SQLite so the server runs with zero external dependencies.
Each tenant has an API key and isolated trace storage.

Schema
------
tenants(id TEXT PK, name TEXT, api_key TEXT UNIQUE, created_at TEXT, plan TEXT)
traces(id TEXT PK, tenant_id TEXT FK, agent_name TEXT, framework TEXT,
       started_at TEXT, finished_at TEXT, success INT, quality_score REAL,
       total_cost_usd REAL, total_tokens INT, cpst REAL, raw_json TEXT)
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import os as _os
_DB_PATH = Path(_os.environ.get("RUNCORE_DB_PATH", ".runcore/cloud.db"))
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _lock:
        con = _conn()
        con.executescript("""
        CREATE TABLE IF NOT EXISTS tenants (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            api_key    TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            plan       TEXT NOT NULL DEFAULT 'free'
        );
        CREATE TABLE IF NOT EXISTS traces (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL REFERENCES tenants(id),
            agent_name    TEXT,
            framework     TEXT,
            task          TEXT,
            started_at    TEXT,
            finished_at   TEXT,
            success       INTEGER,
            quality_score REAL,
            total_cost_usd REAL,
            total_tokens  INTEGER,
            llm_calls     INTEGER,
            tool_calls    INTEGER,
            cpst          REAL,
            raw_json      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_traces_tenant ON traces(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at DESC);
        """)
        con.commit()
        con.close()


def create_tenant(name: str, plan: str = "free") -> dict:
    """Create a new tenant and return its record including the API key."""
    import uuid
    tid = str(uuid.uuid4())
    api_key = "rc_" + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        con = _conn()
        con.execute(
            "INSERT INTO tenants(id, name, api_key, created_at, plan) VALUES(?,?,?,?,?)",
            (tid, name, api_key, now, plan),
        )
        con.commit()
        con.close()
    return {"id": tid, "name": name, "api_key": api_key, "created_at": now, "plan": plan}


def get_tenant_by_key(api_key: str) -> dict | None:
    """Resolve an API key to a tenant record, or None if invalid."""
    with _lock:
        con = _conn()
        row = con.execute(
            "SELECT * FROM tenants WHERE api_key = ?", (api_key,)
        ).fetchone()
        con.close()
    return dict(row) if row else None


def get_tenant_by_id(tenant_id: str) -> dict | None:
    with _lock:
        con = _conn()
        row = con.execute(
            "SELECT * FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone()
        con.close()
    return dict(row) if row else None


def list_tenants() -> list[dict]:
    with _lock:
        con = _conn()
        rows = con.execute(
            "SELECT id, name, created_at, plan FROM tenants ORDER BY created_at DESC"
        ).fetchall()
        con.close()
    return [dict(r) for r in rows]


def ingest_trace(tenant_id: str, atir_dict: dict) -> str:
    """Store a raw ATIR trace dict for a tenant. Returns the trace ID."""
    import uuid
    trace_id = atir_dict.get("trace_id") or str(uuid.uuid4())
    agg = atir_dict.get("aggregates") or {}
    with _lock:
        con = _conn()
        con.execute(
            """INSERT OR REPLACE INTO traces(
                id, tenant_id, agent_name, framework, task,
                started_at, finished_at, success, quality_score,
                total_cost_usd, total_tokens, llm_calls, tool_calls, cpst, raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trace_id,
                tenant_id,
                atir_dict.get("agent_name"),
                atir_dict.get("framework"),
                atir_dict.get("task"),
                atir_dict.get("started_at"),
                atir_dict.get("finished_at"),
                1 if atir_dict.get("success") else 0,
                atir_dict.get("quality_score"),
                agg.get("total_cost_usd"),
                agg.get("total_tokens"),
                agg.get("llm_calls"),
                agg.get("tool_calls"),
                agg.get("cost_per_successful_task"),
                json.dumps(atir_dict),
            ),
        )
        con.commit()
        con.close()
    return trace_id


def list_traces(tenant_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
    with _lock:
        con = _conn()
        rows = con.execute(
            """SELECT id, agent_name, framework, task, started_at, finished_at,
                      success, quality_score, total_cost_usd, total_tokens,
                      llm_calls, tool_calls, cpst
               FROM traces WHERE tenant_id = ?
               ORDER BY started_at DESC LIMIT ? OFFSET ?""",
            (tenant_id, limit, offset),
        ).fetchall()
        con.close()
    return [dict(r) for r in rows]


def get_trace(tenant_id: str, trace_id: str) -> dict | None:
    with _lock:
        con = _conn()
        row = con.execute(
            "SELECT raw_json FROM traces WHERE id = ? AND tenant_id = ?",
            (trace_id, tenant_id),
        ).fetchone()
        con.close()
    return json.loads(row["raw_json"]) if row else None


def tenant_stats(tenant_id: str) -> dict:
    """Aggregate KPIs for a tenant's dashboard."""
    with _lock:
        con = _conn()
        row = con.execute(
            """SELECT
                COUNT(*)                           AS total_traces,
                SUM(success)                       AS successful,
                AVG(total_cost_usd)                AS avg_cost,
                SUM(total_cost_usd)                AS total_cost,
                AVG(total_tokens)                  AS avg_tokens,
                AVG(cpst)                          AS avg_cpst,
                MIN(cpst)                          AS best_cpst,
                COUNT(DISTINCT agent_name)         AS agents,
                MAX(started_at)                    AS last_trace
               FROM traces WHERE tenant_id = ?""",
            (tenant_id,),
        ).fetchone()
        con.close()
    d = dict(row)
    total = d.get("total_traces") or 0
    succ  = d.get("successful") or 0
    d["success_rate"] = round(succ / total * 100, 1) if total else 0
    return d
