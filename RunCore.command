#!/bin/bash
# RunCore — start the dashboard. Double-click this file in Finder.
# Opens the dashboard in your browser. Everything else runs from the UI.

cd "$(dirname "$0")" || exit 1

echo "════════════════════════════════════════════"
echo "  RunCore — Dashboard"
echo "════════════════════════════════════════════"
echo ""

# Ensure setup ran
if [ ! -x ".venv/bin/python" ]; then
  echo "⚠   Not set up yet. Running Setup first…"
  echo ""
  if [ -f "Setup.command" ]; then
    bash Setup.command
  else
    echo "❌  Setup.command not found."
    read -n 1 -s -r -p "Press any key to close…"
    exit 1
  fi
fi

PORT="${PORT:-8765}"
URL="http://127.0.0.1:${PORT}"

# Open the browser shortly after the server starts
( sleep 2; open "$URL" ) &

echo "→  Starting dashboard at $URL"
echo "    (Your browser will open automatically.)"
echo ""
echo "    Leave this window open while you use RunCore."
echo "    Close it (or press Ctrl+C) to stop the server."
echo ""

PORT="$PORT" ./.venv/bin/python serve.py
