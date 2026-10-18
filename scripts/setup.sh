#!/usr/bin/env bash
# One-shot setup on Linux and macOS: installs uv when missing, creates the environment with the
# CPU build of torch, fetches the fine-tuned decision model, then starts the demo.
#
#   scripts/setup.sh            # install everything and start the demo
#   scripts/setup.sh --no-run   # install only
#
# Needs: curl or wget, tar, about 2.5 GB of disk. No Python installation is required: uv
# downloads a private interpreter when the machine has none that fits.
set -euo pipefail
cd "$(dirname "$0")/.."
RUN=1; [ "${1:-}" = "--no-run" ] && RUN=0

say() { printf '\n==> %s\n' "$*"; }

if ! command -v uv >/dev/null 2>&1; then
  say "uv is not installed; installing it from astral.sh (user-local, no sudo)"
  if command -v curl >/dev/null 2>&1; then curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then wget -qO- https://astral.sh/uv/install.sh | sh
  else echo "Neither curl nor wget is available; install uv from https://docs.astral.sh/uv/ and rerun." >&2; exit 1; fi
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || { echo "uv was installed but is not on PATH yet; open a new terminal and rerun scripts/setup.sh" >&2; exit 1; }
fi
say "uv $(uv --version | awk '{print $2}') found"

say "Creating the environment (CPU torch, laya, FastAPI); first run downloads about 400 MB"
uv sync --group ai

say "Fetching the fine-tuned decision model (570 MB release asset)"
scripts/get_model.sh

say "Checking the model loads"
AEI_LAYA_DEVICE=cpu uv run python scripts/check_model.py

if [ "$RUN" = "1" ]; then
  say "Starting the demo: page on http://localhost:8080/, endpoint on http://127.0.0.1:8765 (Ctrl+C stops both)"
  exec scripts/serve_demo.sh
else
  say "Done. Start the demo with scripts/serve_demo.sh"
fi
