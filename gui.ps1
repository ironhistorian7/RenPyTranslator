param([switch]$SourceMode)
$ErrorActionPreference = 'Stop'
$executable = Join-Path $PSScriptRoot 'RenPyTranslator.exe'
if (-not $SourceMode -and (Test-Path -LiteralPath $executable)) {
    Start-Process -FilePath $executable -WindowStyle Hidden
} else {
    $sourceEntry = Join-Path $PSScriptRoot 'app\portable_cli.py'
    $interpreter = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $interpreter) -or -not (Test-Path -LiteralPath $sourceEntry)) {
        throw 'GUI runtime not found. Source users: run .\setup.ps1 first. Portable users: extract the entire ZIP.'
    }
    Start-Process -FilePath $interpreter -ArgumentList @('-B', '-X', 'utf8', ('"' + $sourceEntry + '"'), '--gui') -WindowStyle Hidden
}
