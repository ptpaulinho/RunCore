#!/bin/bash
# RunCore — one-time setup. Double-click this file in Finder.
# Creates a virtual environment and installs everything needed.

cd "$(dirname "$0")" || exit 1

echo "════════════════════════════════════════════"
echo "  RunCore — Setup"
echo "════════════════════════════════════════════"
echo ""

# Find a Python 3.10+ interpreter
PY=""
for cand in python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "❌  Python 3 not found."
  echo "    Install it from https://www.python.org/downloads/ and run Setup again."
  echo ""
  read -n 1 -s -r -p "Press any key to close…"
  exit 1
fi

echo "✓  Using $($PY --version)"
echo ""

# Create venv if missing
if [ ! -d ".venv" ]; then
  echo "→  Creating virtual environment (.venv)…"
  "$PY" -m venv .venv
fi

echo "→  Installing RunCore + all providers (this can take a minute)…"
echo ""
./.venv/bin/python -m pip install --upgrade pip >/dev/null
./.venv/bin/python -m pip install -e ".[all,dev]"

echo ""
echo "════════════════════════════════════════════"
echo "  ✓  Setup complete!"
echo ""
echo "  Next: double-click  RunCore.command  to start the dashboard."
echo "════════════════════════════════════════════"
echo ""
read -n 1 -s -r -p "Press any key to close…"
