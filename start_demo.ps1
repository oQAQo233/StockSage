param(
    [string]$Python = '',
    [int]$Port = 8765,
    [string]$DataDir = '',
    [switch]$NoServer
)
$ErrorActionPreference = 'Stop'
$pythonArgs = @()
if (-not $Python) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if ($pythonCommand) { $Python = $pythonCommand.Source }
    elseif ($launcher) { $Python = $launcher.Source; $pythonArgs = @('-3') }
    elseif (Test-Path -LiteralPath $bundledPython) { $Python = $bundledPython }
    else { throw 'Python 3.10+ is required. Install Python or pass -Python <path>.' }
}
& $Python @pythonArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
$runArgs = @((Join-Path $PSScriptRoot 'run.py'), '--port', "$Port")
if ($DataDir) { $runArgs += @('--data-dir', $DataDir) }
if ($NoServer) { $runArgs += '--no-server' }
& $Python @pythonArgs @runArgs
exit $LASTEXITCODE
