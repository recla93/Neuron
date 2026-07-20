# Neuron installer (Windows) — thin launcher for the UNIFIED Gray Matter
# installer. Gray Matter is always the brain: this installs the GM control
# center + Neuron, registers the gateway, deploys hooks and opens the GUI,
# where you manage/verify the tools. One logic, defined once in
# gray_matter\install.ps1 — this file only finds and launches it.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($gm in @((Join-Path $Here "gray_matter"), (Join-Path (Split-Path -Parent $Here) "gray_matter"))) {
    $inst = Join-Path $gm "install.ps1"
    if (Test-Path $inst) {
        $env:GM_PEER_DIR = $Here
        & powershell -ExecutionPolicy Bypass -File $inst @args
        exit $LASTEXITCODE
    }
}
Write-Host "ERROR: gray_matter not found (bundled .\gray_matter or sibling ..\gray_matter)."
Write-Host "Download the full suite, or place the gray_matter repo next to this one."
exit 1
