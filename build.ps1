param([ValidateSet('Light','Full','Both')][string]$Edition = 'Light')
$ErrorActionPreference = 'Stop'
$interpreter = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $interpreter)) { throw 'Run .\setup.ps1 first. Build dependencies are installed only in .venv.' }
# Windows PowerShell treats redirected native stderr as ErrorRecords. PyInstaller
# writes progress there, so determine success from its exit code, not that stream.
$ErrorActionPreference = 'Continue'
& $interpreter -B -X utf8 (Join-Path $PSScriptRoot 'app\build_portable.py') --edition $Edition.ToLowerInvariant() 2>&1 | ForEach-Object { $_.ToString() }
exit $LASTEXITCODE
