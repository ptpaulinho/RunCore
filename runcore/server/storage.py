"""Tenant-isolated trace storage for RunCore Cloud.

Supports SQLite (default, zero-config) and Postgres (set DATABASE_URL).

  SQLite  — local dev and small deploys; path via RUNCORE_DB_PATH
  Postgres — production; set DATABASE_URL=postgresql://user:pass@host/db

Schema
------
tenants(id, name, api_key, created_at, plan,
        stripe_customer_id, stripe_subscription_id,
        traces_this_month, month_key)
traces(id, tenant_id, agent_name, framework, task,
       started_at, finished_at, success, quality_score,
       total_cost_usd, total_tokens, llm_calls, tool_calls, cpst, raw_json)
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_DB_PATH = Path(os.environ.get("RUNCORE_DB_PATH", ".runcore/cloud.db"))
_POSTGRES = bool(_DATABASE_URL)

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _sqlite_conn():
    import sqlite3
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _pg_conn():
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as exc:
        raise ImportError(
            "psycopg2 is required for Postgres support. "
            "Install with: pip install psycopg2-binary"
        ) from exc
    con = psycopg2.connect(_DATABASE_URL)
    con.autocommit = False
    return con


def _conn():
    return _pg_conn() if _POSTGRES else _sqlite_conn()


from contextlib import contextmanager

@contextmanager
def _db():
    """Context manager that ensures connection is always closed."""
    con = _conn()
    try:
        yield con
    finally:
        con.close()


def _ph() -> str:
    """SQL placeholder: %s for Postgres, ? for SQLite."""
    return "%s" if _POSTGRES else "?"


def _row_to_dict(row) -> dict:
    """Convert a DB row (sqlite3.Row or psycopg2 DictRow) to a plain dict."""
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    # psycopg2 RealDictRow
    return dict(row)


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS tenants (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    email                   TEXT UNIQUE,
    password_hash           TEXT,
    company_name            TEXT,
    api_key                 TEXT UNIQUE NOT NULL,
    created_at              TEXT NOT NULL,
    plan                    TEXT NOT NULL DEFAULT 'free',
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,
    traces_this_month       INTEGER NOT NULL DEFAULT 0,
    month_key               TEXT NOT NULL DEFAULT ''
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
CREATE INDEX IF NOT EXISTS idx_traces_tenant  ON traces(tenant_id);
CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at DESC);
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certifications (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    provider    TEXT,
    model       TEXT,
    suite       TEXT,
    score       REAL,
    grade       TEXT,
    certified   INTEGER,
    n_runs      INTEGER,
    timestamp   TEXT,
    json_data   TEXT,
    html_file   TEXT
);
CREATE INDEX IF NOT EXISTS idx_cert_tenant ON certifications(tenant_id);
"""

_PG_DDL = """
CREATE TABLE IF NOT EXISTS tenants (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    email                   TEXT UNIQUE,
    password_hash           TEXT,
    company_name            TEXT,
    api_key                 TEXT UNIQUE NOT NULL,
    created_at              TEXT NOT NULL,
    plan                    TEXT NOT NULL DEFAULT 'free',
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,
    traces_this_month       INTEGER NOT NULL DEFAULT 0,
    month_key               TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS traces (
    id             TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL REFERENCES tenants(id),
    agent_name     TEXT,
    framework      TEXT,
    task           TEXT,
    started_at     TEXT,
    finished_at    TEXT,
    success        INTEGER,
    quality_score  REAL,
    total_cost_usd REAL,
    total_tokens   INTEGER,
    llm_calls      INTEGER,
    tool_calls     INTEGER,
    cpst           REAL,
    raw_json       TEXT
);
CREATE INDEX IF NOT EXISTS idx_traces_tenant  ON traces(tenant_id);
CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at DESC);
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certifications (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    provider    TEXT,
    model       TEXT,
    suite       TEXT,
    score       REAL,
    grade       TEXT,
    certified   INTEGER,
    n_runs      INTEGER,
    timestamp   TEXT,
    json_data   TEXT,
    html_file   TEXT
);
CREATE INDEX IF NOT EXISTS idx_cert_tenant ON certifications(tenant_id);
"""


def init_db() -> None:
    with _lock, _db() as con:
        if _POSTGRES:
            cur = con.cursor()
            for stmt in _PG_DDL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
            con.commit()
            cur.close()
        else:
            con.executescript(_SQLITE_DDL)
            con.commit()


# ---------------------------------------------------------------------------
# Current month key helper (YYYY-MM)
# ---------------------------------------------------------------------------

def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Tenant CRUD
# ---------------------------------------------------------------------------

def create_tenant(name: str, plan: str = "free") -> dict:
    tid = str(uuid.uuid4())
    api_key = "rc_" + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    mk = _month_key()
    ph = _ph()
    with _lock, _db() as con:
        if _POSTGRES:
            cur = con.cursor()
            cur.execute(
                f"INSERT INTO tenants(id,name,api_key,created_at,plan,month_key) "
                f"VALUES({ph},{ph},{ph},{ph},{ph},{ph})",
                (tid, name, api_key, now, plan, mk),
            )
            con.commit()
            cur.close()
        else:
            con.execute(
                "INSERT INTO tenants(id,name,api_key,created_at,plan,month_key) "
                "VALUES(?,?,?,?,?,?)",
                (tid, name, api_key, now, plan, mk),
            )
            con.commit()
    return {
        "id": tid, "name": name, "api_key": api_key,
        "created_at": now, "plan": plan,
        "traces_this_month": 0, "month_key": mk,
    }


def get_tenant_by_key(api_key: str) -> dict | None:
    ph = _ph()
    with _lock, _db() as con:
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute(f"SELECT * FROM tenants WHERE api_key={ph}", (api_key,))
            row = cur.fetchone()
            cur.close()
        else:
            row = con.execute(f"SELECT * FROM tenants WHERE api_key={ph}", (api_key,)).fetchone()
    return dict(row) if row else None


def get_tenant_by_id(tenant_id: str) -> dict | None:
    ph = _ph()
    with _lock, _db() as con:
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute(f"SELECT * FROM tenants WHERE id={ph}", (tenant_id,))
            row = cur.fetchone()
            cur.close()
        else:
            row = con.execute(f"SELECT * FROM tenants WHERE id={ph}", (tenant_id,)).fetchone()
    return dict(row) if row else None


def list_tenants() -> list[dict]:
    with _lock, _db() as con:
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute("SELECT id,name,created_at,plan FROM tenants ORDER BY created_at DESC")
            rows = cur.fetchall()
            cur.close()
        else:
            rows = con.execute(
                "SELECT id,name,created_at,plan FROM tenants ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def upgrade_tenant_plan(
    tenant_id: str,
    plan: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
) -> None:
    """Set a tenant's plan (and optionally Stripe IDs) after successful checkout."""
    ph = _ph()
    with _lock, _db() as con:
        if _POSTGRES:
            cur = con.cursor()
            cur.execute(
                f"UPDATE tenants SET plan={ph}, stripe_customer_id=COALESCE({ph}, stripe_customer_id), "
                f"stripe_subscription_id=COALESCE({ph}, stripe_subscription_id) WHERE id={ph}",
                (plan, stripe_customer_id, stripe_subscription_id, tenant_id),
            )
            con.commit()
            cur.close()
        else:
            con.execute(
                "UPDATE tenants SET plan=?, stripe_customer_id=COALESCE(?,stripe_customer_id), "
                "stripe_subscription_id=COALESCE(?,stripe_subscription_id) WHERE id=?",
                (plan, stripe_customer_id, stripe_subscription_id, tenant_id),
            )
            con.commit()


def downgrade_tenant_by_customer(stripe_customer_id: str, plan: str = "free") -> None:
    """Downgrade a tenant (found by Stripe customer ID) to *plan*."""
    ph = _ph()
    with _lock, _db() as con:
        if _POSTGRES:
            cur = con.cursor()
            cur.execute(
                f"UPDATE tenants SET plan={ph} WHERE stripe_customer_id={ph}",
                (plan, stripe_customer_id),
            )
            con.commit()
            cur.close()
        else:
            con.execute(
                "UPDATE tenants SET plan=? WHERE stripe_customer_id=?",
                (plan, stripe_customer_id),
            )
            con.commit()


# ---------------------------------------------------------------------------
# Trace storage
# ---------------------------------------------------------------------------

def _reset_month_counter_if_needed(con, tenant_id: str, current_month: str) -> None:
    """Reset traces_this_month to 0 when the month rolls over."""
    ph = _ph()
    if _POSTGRES:
        cur = con.cursor()
        cur.execute(
            f"UPDATE tenants SET traces_this_month=0, month_key={ph} "
            f"WHERE id={ph} AND month_key!={ph}",
            (current_month, tenant_id, current_month),
        )
        cur.close()
    else:
        con.execute(
            "UPDATE tenants SET traces_this_month=0, month_key=? WHERE id=? AND month_key!=?",
            (current_month, tenant_id, current_month),
        )


def ingest_trace(tenant_id: str, atir_dict: dict) -> str:
    """Store a raw ATIR trace dict. Returns the trace ID. Increments monthly usage counter."""
    trace_id = atir_dict.get("trace_id") or str(uuid.uuid4())
    agg = atir_dict.get("aggregates") or {}
    mk = _month_key()
    ph = _ph()

    upsert = (
        f"INSERT INTO traces(id,tenant_id,agent_name,framework,task,"
        f"started_at,finished_at,success,quality_score,"
        f"total_cost_usd,total_tokens,llm_calls,tool_calls,cpst,raw_json)"
        f" VALUES({','.join([ph]*15)})"
    )
    values = (
        trace_id, tenant_id,
        atir_dict.get("agent_name"), atir_dict.get("framework"), atir_dict.get("task"),
        atir_dict.get("started_at"), atir_dict.get("finished_at"),
        1 if atir_dict.get("success") else 0,
        atir_dict.get("quality_score"),
        agg.get("total_cost_usd"), agg.get("total_tokens"),
        agg.get("llm_calls"), agg.get("tool_calls"),
        agg.get("cost_per_successful_task"),
        json.dumps(atir_dict),
    )

    with _lock, _db() as con:
        _reset_month_counter_if_needed(con, tenant_id, mk)
        if _POSTGRES:
            cur = con.cursor()
            cur.execute(
                upsert + " ON CONFLICT(id) DO UPDATE SET "
                "agent_name=EXCLUDED.agent_name, framework=EXCLUDED.framework, "
                "task=EXCLUDED.task, finished_at=EXCLUDED.finished_at, "
                "success=EXCLUDED.success, quality_score=EXCLUDED.quality_score, "
                "total_cost_usd=EXCLUDED.total_cost_usd, total_tokens=EXCLUDED.total_tokens, "
                "llm_calls=EXCLUDED.llm_calls, tool_calls=EXCLUDED.tool_calls, "
                "cpst=EXCLUDED.cpst, raw_json=EXCLUDED.raw_json",
                values,
            )
            cur.execute(
                f"UPDATE tenants SET traces_this_month=traces_this_month+1 WHERE id={ph}",
                (tenant_id,),
            )
            con.commit()
            cur.close()
        else:
            con.execute("INSERT OR REPLACE INTO traces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            con.execute(
                "UPDATE tenants SET traces_this_month=traces_this_month+1 WHERE id=?",
                (tenant_id,),
            )
            con.commit()
    return trace_id


def get_monthly_usage(tenant_id: str) -> int:
    """Return how many traces have been ingested this month for this tenant."""
    tenant = get_tenant_by_id(tenant_id)
    if not tenant:
        return 0
    if tenant.get("month_key", "") != _month_key():
        return 0
    return tenant.get("traces_this_month") or 0


def list_traces(tenant_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
    ph = _ph()
    with _lock, _db() as con:
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute(
                f"SELECT id,agent_name,framework,task,started_at,finished_at,"
                f"success,quality_score,total_cost_usd,total_tokens,llm_calls,tool_calls,cpst"
                f" FROM traces WHERE tenant_id={ph} ORDER BY started_at DESC LIMIT {ph} OFFSET {ph}",
                (tenant_id, limit, offset),
            )
            rows = cur.fetchall()
            cur.close()
        else:
            rows = con.execute(
                "SELECT id,agent_name,framework,task,started_at,finished_at,"
                "success,quality_score,total_cost_usd,total_tokens,llm_calls,tool_calls,cpst"
                " FROM traces WHERE tenant_id=? ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (tenant_id, limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def get_trace(tenant_id: str, trace_id: str) -> dict | None:
    ph = _ph()
    with _lock, _db() as con:
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute(
                f"SELECT raw_json FROM traces WHERE id={ph} AND tenant_id={ph}",
                (trace_id, tenant_id),
            )
            row = cur.fetchone()
            cur.close()
        else:
            row = con.execute(
                "SELECT raw_json FROM traces WHERE id=? AND tenant_id=?",
                (trace_id, tenant_id),
            ).fetchone()
    return json.loads(row["raw_json"]) if row else None


def tenant_stats(tenant_id: str) -> dict:
    ph = _ph()
    with _lock, _db() as con:
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute(
                f"SELECT COUNT(*) AS total_traces, SUM(success) AS successful,"
                f" AVG(total_cost_usd) AS avg_cost, SUM(total_cost_usd) AS total_cost,"
                f" AVG(total_tokens) AS avg_tokens, AVG(cpst) AS avg_cpst,"
                f" MIN(cpst) AS best_cpst, COUNT(DISTINCT agent_name) AS agents,"
                f" MAX(started_at) AS last_trace"
                f" FROM traces WHERE tenant_id={ph}",
                (tenant_id,),
            )
            row = cur.fetchone()
            cur.close()
        else:
            row = con.execute(
                "SELECT COUNT(*) AS total_traces, SUM(success) AS successful,"
                " AVG(total_cost_usd) AS avg_cost, SUM(total_cost_usd) AS total_cost,"
                " AVG(total_tokens) AS avg_tokens, AVG(cpst) AS avg_cpst,"
                " MIN(cpst) AS best_cpst, COUNT(DISTINCT agent_name) AS agents,"
                " MAX(started_at) AS last_trace"
                " FROM traces WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
    d = dict(row) if row else {}
    total = d.get("total_traces") or 0
    succ = d.get("successful") or 0
    d["success_rate"] = round(succ / total * 100, 1) if total else 0
    return d


# ---------------------------------------------------------------------------
# Auth — register, login, session management
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    import hashlib, os
    salt = os.urandom(16).hex()
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"


def _verify_password(password: str, stored: str) -> bool:
    import hashlib
    try:
        salt, h = stored.split(":", 1)
        return hashlib.sha256((salt + password).encode()).hexdigest() == h
    except Exception:
        return False


def register_tenant(email: str, password: str, company_name: str) -> dict:
    """Create a new tenant account. Returns tenant dict or raises ValueError."""
    with _lock, _db() as con:
        ph = "%s" if _POSTGRES else "?"
        existing = None
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute(f"SELECT id FROM tenants WHERE email={ph}", (email,))
            existing = cur.fetchone()
            cur.close()
        else:
            existing = con.execute(f"SELECT id FROM tenants WHERE email={ph}", (email,)).fetchone()
        if existing:
            raise ValueError("Email already registered")

    tenant_id = str(uuid.uuid4())
    api_key = f"rc_{secrets.token_urlsafe(32)}"
    now = datetime.now(timezone.utc).isoformat()
    pw_hash = _hash_password(password)

    with _lock, _db() as con:
        ph = "%s" if _POSTGRES else "?"
        if _POSTGRES:
            cur = con.cursor()
            cur.execute(
                f"INSERT INTO tenants(id,name,email,password_hash,company_name,api_key,created_at,plan,month_key)"
                f" VALUES({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (tenant_id, company_name, email, pw_hash, company_name, api_key, now, "free", now[:7]),
            )
            con.commit(); cur.close()
        else:
            con.execute(
                "INSERT INTO tenants(id,name,email,password_hash,company_name,api_key,created_at,plan,month_key)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, company_name, email, pw_hash, company_name, api_key, now, "free", now[:7]),
            )
    return {"id": tenant_id, "email": email, "company_name": company_name, "api_key": api_key}


def login_tenant(email: str, password: str) -> dict | None:
    """Verify credentials and return a session token, or None on failure."""
    with _lock, _db() as con:
        ph = "%s" if _POSTGRES else "?"
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute(f"SELECT * FROM tenants WHERE email={ph}", (email,))
            row = cur.fetchone(); cur.close()
        else:
            row = con.execute(f"SELECT * FROM tenants WHERE email={ph}", (email,)).fetchone()
    if not row:
        return None
    tenant = dict(row)
    if not _verify_password(password, tenant.get("password_hash", "")):
        return None

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    from datetime import timedelta
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    with _lock, _db() as con:
        ph = "%s" if _POSTGRES else "?"
        if _POSTGRES:
            cur = con.cursor()
            cur.execute(f"INSERT INTO sessions(token,tenant_id,created_at,expires_at) VALUES({ph},{ph},{ph},{ph})",
                        (token, tenant["id"], now, expires))
            con.commit(); cur.close()
        else:
            con.execute("INSERT INTO sessions(token,tenant_id,created_at,expires_at) VALUES(?,?,?,?)",
                        (token, tenant["id"], now, expires))
    return {"token": token, "tenant": tenant}


def get_session(token: str) -> dict | None:
    """Return tenant dict for a valid session token, or None."""
    if not token:
        return None
    with _lock, _db() as con:
        ph = "%s" if _POSTGRES else "?"
        now = datetime.now(timezone.utc).isoformat()
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute(f"SELECT t.* FROM sessions s JOIN tenants t ON t.id=s.tenant_id"
                        f" WHERE s.token={ph} AND s.expires_at>{ph}", (token, now))
            row = cur.fetchone(); cur.close()
        else:
            row = con.execute("SELECT t.* FROM sessions s JOIN tenants t ON t.id=s.tenant_id"
                              " WHERE s.token=? AND s.expires_at>?", (token, now)).fetchone()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    with _lock, _db() as con:
        ph = "%s" if _POSTGRES else "?"
        if _POSTGRES:
            cur = con.cursor()
            cur.execute(f"DELETE FROM sessions WHERE token={ph}", (token,))
            con.commit(); cur.close()
        else:
            con.execute("DELETE FROM sessions WHERE token=?", (token,))


def save_certification(tenant_id: str, cert: dict, html_file: str = "") -> str:
    """Persist a certification result for a tenant."""
    cert_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _db() as con:
        ph = "%s" if _POSTGRES else "?"
        vals = (cert_id, tenant_id, cert.get("provider"), cert.get("model"),
                cert.get("suite"), cert.get("overall"), cert.get("grade"),
                1 if cert.get("certified") else 0, cert.get("n_runs"),
                cert.get("timestamp", now), json.dumps(cert), html_file)
        sql = (f"INSERT INTO certifications(id,tenant_id,provider,model,suite,score,grade,"
               f"certified,n_runs,timestamp,json_data,html_file) VALUES"
               f"({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})")
        if _POSTGRES:
            cur = con.cursor(); cur.execute(sql, vals); con.commit(); cur.close()
        else:
            con.execute(sql, vals)
    return cert_id


def list_certifications(tenant_id: str) -> list[dict]:
    with _lock, _db() as con:
        ph = "%s" if _POSTGRES else "?"
        if _POSTGRES:
            import psycopg2.extras
            con.cursor_factory = psycopg2.extras.RealDictCursor
            cur = con.cursor()
            cur.execute(f"SELECT * FROM certifications WHERE tenant_id={ph} ORDER BY timestamp DESC", (tenant_id,))
            rows = cur.fetchall(); cur.close()
        else:
            rows = con.execute("SELECT * FROM certifications WHERE tenant_id=? ORDER BY timestamp DESC",
                               (tenant_id,)).fetchall()
    return [dict(r) for r in rows]
