$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

$env:HERMES_HOME = 'D:\my-project\AI\Hermes\data\profiles\marketing-agent'
& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' compose -f (Join-Path $ProjectRoot 'docker-compose.yml') up -d db
& $Python -m alembic upgrade head
& $Python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

