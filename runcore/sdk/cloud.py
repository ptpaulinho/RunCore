"""RunCore SDK — Cloud auto-push configuration.

Call once at startup to enable automatic trace uploading::

    import runcore
    runcore.configure(
        api_key="rc_...",
        endpoint="https://your-runcore.onrender.com",  # optional, defaults to hosted cloud
    )

After configure(), every ``with runcore.capture(...)`` block will automatically
push the resulting ATIR trace to the Cloud ingest API when the context exits.

Thread safety: the config is module-level and set once; reads are lock-free.
Pushes are fire-and-forget on a daemon thread so they never block the caller.
"""
from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runcore.atir.spec import ATIRTrace

# ---------------------------------------------------------------------------
# Module-level config (set by runcore.configure())
# ---------------------------------------------------------------------------

_DEFAULT_ENDPOINT = os.environ.get(
    "RUNCORE_CLOUD_ENDPOINT", "https://runcore.onrender.com"
)

_config: dict = {
    "api_key": os.environ.get("RUNCORE_API_KEY", ""),
    "endpoint": os.environ.get("RUNCORE_CLOUD_ENDPOINT", _DEFAULT_ENDPOINT),
    "auto_push": False,          # only True after configure() is called with a key
    "timeout_s": 5.0,
    "on_error": "warn",          # "warn" | "raise" | "silent"
}
_config_lock = threading.Lock()

# Push stats (for observability)
_stats = {"pushed": 0, "errors": 0}
_stats_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure(
    api_key: str,
    endpoint: str | None = None,
    timeout_s: float = 5.0,
    on_error: str = "warn",
) -> None:
    """Enable automatic trace push to RunCore Cloud.

    Args:
        api_key:   API key starting with ``rc_`` — obtain from ``POST /cloud/tenants``.
        endpoint:  Base URL of your RunCore Cloud instance.
                   Defaults to the ``RUNCORE_CLOUD_ENDPOINT`` env var or the hosted endpoint.
        timeout_s: HTTP request timeout in seconds (default 5).
        on_error:  What to do when a push fails:
                   ``"warn"`` (default) — print a warning and continue,
                   ``"raise"``          — re-raise the exception,
                   ``"silent"``         — ignore silently.
    """
    if not api_key:
        raise ValueError("api_key must not be empty.")
    if not api_key.startswith("rc_"):
        raise ValueError("api_key must start with 'rc_'.")
    if on_error not in ("warn", "raise", "silent"):
        raise ValueError("on_error must be 'warn', 'raise', or 'silent'.")

    with _config_lock:
        _config["api_key"] = api_key
        _config["endpoint"] = (endpoint or _DEFAULT_ENDPOINT).rstrip("/")
        _config["auto_push"] = True
        _config["timeout_s"] = timeout_s
        _config["on_error"] = on_error


def get_config() -> dict:
    """Return a copy of the current cloud config."""
    with _config_lock:
        return dict(_config)


def is_configured() -> bool:
    """Return True if auto-push is enabled (configure() was called with a key)."""
    return bool(_config.get("auto_push") and _config.get("api_key"))


def reset() -> None:
    """Reset cloud config to defaults. Mainly for tests."""
    with _config_lock:
        _config["api_key"] = os.environ.get("RUNCORE_API_KEY", "")
        _config["endpoint"] = _DEFAULT_ENDPOINT
        _config["auto_push"] = False
        _config["timeout_s"] = 5.0
        _config["on_error"] = "warn"
    with _stats_lock:
        _stats["pushed"] = 0
        _stats["errors"] = 0


def push_stats() -> dict:
    """Return push statistics (pushed count, error count)."""
    with _stats_lock:
        return dict(_stats)


# ---------------------------------------------------------------------------
# Push logic
# ---------------------------------------------------------------------------

def push_trace(trace: "ATIRTrace", *, block: bool = False) -> None:
    """Push a single ATIR trace to the Cloud ingest endpoint.

    By default (``block=False``) the push runs on a daemon thread so it never
    delays the caller. Pass ``block=True`` to wait for completion (useful in tests).
    """
    if not is_configured():
        return

    cfg = get_config()

    def _do_push():
        try:
            _push_sync(trace, cfg)
            with _stats_lock:
                _stats["pushed"] += 1
        except Exception as exc:
            with _stats_lock:
                _stats["errors"] += 1
            on_error = cfg.get("on_error", "warn")
            if on_error == "raise":
                raise
            elif on_error == "warn":
                import warnings
                warnings.warn(
                    f"[RunCore] Failed to push trace {trace.trace_id} to Cloud: {exc}",
                    stacklevel=4,
                )
            # "silent" — do nothing

    if block:
        _do_push()
    else:
        t = threading.Thread(target=_do_push, daemon=True, name="runcore-push")
        t.start()


def _push_sync(trace: "ATIRTrace", cfg: dict) -> None:
    """Perform the actual HTTP POST — called from push_trace()."""
    try:
        import urllib.request
        import urllib.error
        import json
    except ImportError:
        return  # stdlib always available, just in case

    endpoint = cfg["endpoint"]
    api_key = cfg["api_key"]
    timeout = cfg.get("timeout_s", 5.0)

    payload = json.dumps({"traces": [trace.model_dump(mode="json")]}).encode()
    req = urllib.request.Request(
        f"{endpoint}/cloud/ingest",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status not in (200, 201):
                body = resp.read(256).decode(errors="replace")
                raise RuntimeError(f"HTTP {resp.status}: {body}")
    except urllib.error.HTTPError as exc:
        body = exc.read(256).decode(errors="replace") if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
