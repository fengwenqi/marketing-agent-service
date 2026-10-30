$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

$env:HERMES_HOME = 'D:\my-project\AI\Hermes\data\profiles\marketing-agent'
# 通过 PATH 查找 Docker，避免把 Docker Desktop 的安装磁盘写死在脚本中。
& docker compose -f (Join-Path $ProjectRoot 'docker-compose.yml') up -d db
& $Python -m alembic upgrade head
& $Python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

