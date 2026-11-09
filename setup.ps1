param([string]$PythonExe)
$ErrorActionPreference = 'Stop'
$toolRoot = $PSScriptRoot
$venvPython = Join-Path $toolRoot '.venv\Scripts\python.exe'
function Test-DeveloperPython([string]$Executable, [string[]]$PrefixArgs = @()) {
    try {
        & $Executable @PrefixArgs -c "import sys,struct;sys.exit(0 if sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
if (Test-Path -LiteralPath $venvPython) {
    if (-not (Test-DeveloperPython $venvPython)) {
        throw 'The existing .venv needs Python 3.12 x64. It was not changed. Rename it before running setup again.'
    }
} else {
    $candidate = $null
    $prefixArgs = @()
    if ($PythonExe) {
        if (Test-DeveloperPython $PythonExe) { $candidate = $PythonExe }
    } else {
        $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pyCommand -and (Test-DeveloperPython $pyCommand.Source @('-3.12'))) {
            $candidate = $pyCommand.Source
            $prefixArgs = @('-3.12')
        } else {
            $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($pythonCommand -and (Test-DeveloperPython $pythonCommand.Source)) { $candidate = $pythonCommand.Source }
        }
    }
    if (-not $candidate) {
        throw 'Install Python 3.12 (Windows x64) from https://www.python.org/downloads/windows/ and run setup.ps1 again. Or use: .\setup.ps1 -PythonExe "C:\path\python.exe". Portable ZIP users do not need Python.'
    }
    & $candidate @prefixArgs -m venv (Join-Path $toolRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the repository-local .venv.' }
}
$ErrorActionPreference = 'Continue'
& $venvPython -B -X utf8 (Join-Path $toolRoot 'app\development_setup.py') 2>&1 | ForEach-Object { $_.ToString() }
exit $LASTEXITCODE
