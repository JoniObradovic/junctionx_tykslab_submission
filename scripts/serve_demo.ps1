# Run the demo on Windows: the page on http://localhost:8080/ and the Ask endpoint (rules + the
# Laya decision model on CPU) on http://127.0.0.1:8765. Ctrl+C stops both.
#
#   powershell -ExecutionPolicy Bypass -File scripts\serve_demo.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$pagePort = if ($env:PAGE_PORT) { $env:PAGE_PORT } else { "8080" }
$aiPort = if ($env:AI_PORT) { $env:AI_PORT } else { "8765" }
$env:AEI_LAYA_DEVICE = if ($env:AEI_LAYA_DEVICE) { $env:AEI_LAYA_DEVICE } else { "cpu" }

$endpoint = Start-Process -FilePath "uv" -ArgumentList @("run", "uvicorn", "ai.serve:app", "--host", "127.0.0.1", "--port", $aiPort) -NoNewWindow -PassThru
Write-Host "Ask endpoint: http://127.0.0.1:$aiPort (health: /health)"
Write-Host "Demo page:    http://localhost:$pagePort/"
try {
  uv run python -m http.server -d web $pagePort --bind 127.0.0.1
} finally {
  if ($endpoint -and -not $endpoint.HasExited) { Stop-Process -Id $endpoint.Id -Force }
}
