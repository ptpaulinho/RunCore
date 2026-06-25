"""Launch runcore dashboard — reads PORT from environment."""
import os
import sys

# Ensure benchmarks/ is importable when running from the repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

port = int(os.environ.get("PORT", 8765))
# Bind to 0.0.0.0 in production (Render, Railway, etc.), localhost otherwise
host = "0.0.0.0" if os.environ.get("RUNCORE_ENV") == "production" else "127.0.0.1"
uvicorn.run("runcore.server.app:app", host=host, port=port, reload=False)
