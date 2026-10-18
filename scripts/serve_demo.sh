#!/usr/bin/env bash
# Run the demo locally: the page on http://localhost:8080/ and, when the `ai` dependency group is
# installed, the Ask endpoint (rules + the Laya decision model on CPU) on http://127.0.0.1:8765.
# The page finds the endpoint by itself on localhost; without it the Ask box runs the rules alone.
#
#   scripts/setup.sh              # once; installs uv if needed, the environment and the model
#   scripts/serve_demo.sh         # then open http://localhost:8080/
set -euo pipefail
cd "$(dirname "$0")/.."
PAGE_PORT="${PAGE_PORT:-8080}"
AI_PORT="${AI_PORT:-8765}"
if uv run --group ai python -c "import laya, fastapi" >/dev/null 2>&1; then
  AEI_LAYA_DEVICE="${AEI_LAYA_DEVICE:-cpu}" uv run --group ai uvicorn ai.serve:app --host 127.0.0.1 --port "$AI_PORT" &
  AI_PID=$!
  trap 'kill $AI_PID 2>/dev/null || true' EXIT
  echo "Ask endpoint: http://127.0.0.1:$AI_PORT (health: /health)"
else
  echo "Ask endpoint not started: run 'uv sync --group ai' to enable the decision model. The page falls back to the rules."
fi
echo "Demo page:    http://localhost:$PAGE_PORT/"
uv run python -m http.server -d web "$PAGE_PORT" --bind 127.0.0.1
