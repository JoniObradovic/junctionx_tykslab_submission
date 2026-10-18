# One-shot setup on Windows (PowerShell 5.1 or 7): installs uv when missing, creates the
# environment with the CPU build of torch, fetches the fine-tuned decision model, then starts the
# demo. Run from the repository folder:
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1          # install and start
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -NoRun   # install only
#
# Needs about 2.5 GB of disk. No Python installation is required: uv downloads a private
# interpreter when the machine has none that fits. tar.exe ships with Windows 10 and later.
param([switch]$NoRun)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Say($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Say "uv is not installed; installing it from astral.sh (user-local, no administrator rights)"
  Invoke-Expression (Invoke-RestMethod https://astral.sh/uv/install.ps1)
  $env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv was installed but is not on PATH yet; open a new terminal and rerun scripts\setup.ps1" }
}
Say "uv $((uv --version) -replace 'uv ','') found"

Say "Creating the environment (CPU torch, laya, FastAPI); first run downloads about 400 MB"
uv sync --group ai
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

$dest = "ai\laya_finetune\model"
if (Test-Path "$dest\model.safetensors") {
  Say "Decision model already present in $dest"
} else {
  Say "Fetching the fine-tuned decision model (570 MB release asset)"
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  $asset = "laya-guard-model.tar.gz"
  $url = "https://github.com/JoniObradovic/junctionx_tykslab_submission/releases/latest/download/$asset"
  if (Get-Command gh -ErrorAction SilentlyContinue) {
    # a signed-in GitHub CLI also works while the repository is private
    gh release download --repo JoniObradovic/junctionx_tykslab_submission --pattern $asset --dir $dest
    if ($LASTEXITCODE -ne 0) { throw "gh release download failed" }
  } else {
    try { Invoke-WebRequest -Uri $url -OutFile "$dest\$asset" }
    catch { throw "Download failed. While the repository is private the asset needs a signed-in GitHub CLI (winget install GitHub.cli; gh auth login), then rerun." }
  }
  tar -xzf "$dest\$asset" -C $dest
  Remove-Item "$dest\$asset"
}

Say "Checking the model loads"
$env:AEI_LAYA_DEVICE = "cpu"
uv run python scripts\check_model.py
if ($LASTEXITCODE -ne 0) { throw "the model did not load" }

if ($NoRun) { Say "Done. Start the demo with scripts\serve_demo.ps1"; exit 0 }
Say "Starting the demo: page on http://localhost:8080/, endpoint on http://127.0.0.1:8765 (Ctrl+C stops both)"
& (Join-Path $PSScriptRoot "serve_demo.ps1")
