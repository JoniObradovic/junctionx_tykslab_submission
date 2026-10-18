#!/usr/bin/env bash
# Fetch the fine-tuned Laya checkpoint (about 650 MB) into ai/laya_finetune/model/.
# The checkpoint is published as a GitHub release asset of this repository, not as git objects.
#
#   scripts/get_model.sh                 # latest release
#   scripts/get_model.sh v1.0.0          # a specific tag
#
# Needs gh (signed in) or curl or wget, and tar.
# Alternative without this script: set AEI_LAYA_HUB=<org>/<repo> to a Hugging Face Hub copy and
# the endpoint downloads it on first start.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-}"
DEST="ai/laya_finetune/model"
ASSET="laya-guard-model.tar.gz"
if [ -f "$DEST/model.safetensors" ]; then echo "already present: $DEST"; exit 0; fi
mkdir -p "$DEST"
REPO="${GITHUB_REPOSITORY:-JoniObradovic/junctionx_tykslab_submission}"
if [ -n "$TAG" ]; then URL="https://github.com/$REPO/releases/download/$TAG/$ASSET"; else URL="https://github.com/$REPO/releases/latest/download/$ASSET"; fi
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  # a signed-in gh also works while the repository is private
  if [ -n "$TAG" ]; then gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --dir "$DEST"; else gh release download --repo "$REPO" --pattern "$ASSET" --dir "$DEST"; fi
elif command -v curl >/dev/null 2>&1; then
  curl -L --fail --progress-bar -o "$DEST/$ASSET" "$URL" || { echo "Download failed. While the repository is private the asset needs a signed-in GitHub CLI: install gh, run 'gh auth login', then rerun." >&2; rm -f "$DEST/$ASSET"; exit 1; }
else
  wget -O "$DEST/$ASSET" "$URL" || { echo "Download failed. While the repository is private the asset needs a signed-in GitHub CLI: install gh, run 'gh auth login', then rerun." >&2; rm -f "$DEST/$ASSET"; exit 1; }
fi
tar -xzf "$DEST/$ASSET" -C "$DEST" && rm -f "$DEST/$ASSET"
echo "checkpoint ready: $DEST"
