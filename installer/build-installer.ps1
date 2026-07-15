param(
    [string]$Output = "$(Join-Path $PSScriptRoot '..\NeuronInstaller.exe')"
)

$ErrorActionPreference = 'Stop'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "C# compiler not found: $csc" }
$outDir = Split-Path -Parent $Output
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
& $csc /nologo /target:winexe /optimize+ /out:$Output `
    /reference:System.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll `
    (Join-Path $PSScriptRoot 'NeuronInstaller.cs')
if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed." }
Write-Host "Built $Output"
