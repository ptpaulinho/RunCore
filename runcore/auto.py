"""Zero-config capture: add one line and every LLM call is recorded.

    import runcore.auto   # first line of your script

- Patches the OpenAI / Anthropic clients (whichever are installed).
- Records the whole run as one trace (named after your script).
- At exit: if ``RUNCORE_API_KEY`` is set, the trace is sent to your RunCore
  dashboard; otherwise it is saved to ``runcore_trace.json`` so you can drag it
  into the dashboard's upload box.

Env vars: ``RUNCORE_API_KEY``, ``RUNCORE_AGENT_NAME``, ``RUNCORE_TRACE_FILE``.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
from pathlib import Path

from runcore.sdk import cloud as _cloud
from runcore.sdk import context as _ctx
from runcore.sdk.capture import Capture
from runcore.sdk.instrument import auto_instrument

# ponytail: one trace per process on the main thread; use runcore.capture() per request in servers.
auto_instrument()
_name = os.environ.get("RUNCORE_AGENT_NAME") or Path(sys.argv[0] or "agent").stem or "agent"
capture = Capture(agent_name=_name, framework="auto")
_ctx.push(capture)

_key = os.environ.get("RUNCORE_API_KEY", "")
if _key.startswith("rc_"):
    _cloud.configure(api_key=_key)


def _finish() -> None:
    _ctx.pop()
    if not capture._spans:
        return  # nothing called an LLM — don't create empty traces
    trace = capture.get_atir()
    if _cloud.is_configured():
        _cloud.push_trace(trace, block=True)
        print(f"[RunCore] trace sent → {_cloud.get_config()['endpoint']}/cloud/dashboard", file=sys.stderr)
    else:
        path = Path(os.environ.get("RUNCORE_TRACE_FILE", "runcore_trace.json"))
        path.write_text(json.dumps(trace.model_dump(mode="json"), indent=2))
        print(f"[RunCore] trace saved → {path.resolve()} (upload it at /app/dashboard)", file=sys.stderr)


atexit.register(_finish)
