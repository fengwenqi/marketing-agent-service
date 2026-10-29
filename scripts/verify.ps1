$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

Push-Location $ProjectRoot
try {
    & $Python -m ruff check app tests
    & $Python -m pytest -q
} finally {
    Pop-Location
}

