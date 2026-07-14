<#
.SYNOPSIS
    Neuron Configuration Center — thin launcher (v5.4+).
.DESCRIPTION
    The 2700-line PowerShell "Configuration Center" was retired: its features now
    live in the cross-platform Python CLI, the single source of truth.

        install / repair / status / uninstall   ->  neuron setup
        overview / export / consolidate /
        visualize / doctor / bridge / tunnel /
        console / connect (Turso Cloud)          ->  neuron manage

    This launcher just resolves the right Python and opens `neuron manage`. For
    first-time install use Neuron.bat (or run install.ps1 once).
.NOTES
    Kept so Configuration.bat and existing shortcuts keep working.
#>
param([string]$Slug = 'neuron5')

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:NEURON_SLUG) { $env:NEURON_SLUG = $Slug }

# Resolve the interpreter: prefer the install venv, then a real Python, then py.
$venvPy = Join-Path $env:LOCALAPPDATA "Programs\$($env:NEURON_SLUG)\.venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $py = $venvPy; $pyArgs = @('-m', 'neuron', 'manage')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $py = 'python'; $pyArgs = @('-m', 'neuron', 'manage')
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $py = 'py'; $pyArgs = @('-3', '-m', 'neuron', 'manage')
} else {
    Write-Host "Neuron is not installed yet. Run Neuron.bat (or install.ps1) first." -ForegroundColor Yellow
    exit 1
}

& $py @pyArgs
exit $LASTEXITCODE
