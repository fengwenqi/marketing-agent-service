# Marketing Agent Service

恒智影营销云的 FastAPI 业务服务。当前版本完成活动上下文采集骨架：活动状态机、Gate 模型、结构化 Skill 输出、Hermes Gateway 适配和 PostgreSQL 表结构。

## 本地结构

- FastAPI 在 Windows 的独立 Python 3.12 `.venv` 中运行。
- PostgreSQL 16 由 Docker Desktop 管理。
- Hermes Agent 与 Gateway 使用现有 `marketing-agent` Profile。

## 首次启动

```powershell
cd D:\my-project\AI\Hermes\marketing-agent-service
Copy-Item .env.example .env
docker compose up -d db
.\.venv\Scripts\python.exe -m alembic upgrade head
$env:HERMES_HOME = 'D:\my-project\AI\Hermes\data\profiles\marketing-agent'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

打开 `http://127.0.0.1:8000/docs` 使用 Swagger。所有写请求都必须传 `Idempotency-Key`，用于避免前端重试导致重复活动或重复 Agent 执行。

## 当前 API

- `GET /health`
- `POST /api/v1/campaigns`
- `GET /api/v1/campaigns/{campaign_id}`
- `POST /api/v1/campaigns/{campaign_id}/messages`

## 代码阅读建议

1. `app/campaigns/state_machine.py`：后端如何阻止跨阶段跳转。
2. `app/hermes/contracts.py`：如何用 Pydantic 把 LLM 输出变成可信数据。
3. `app/hermes/gateway_adapter.py`：FastAPI 如何创建指定 Skill 的 Hermes 会话。
4. `app/campaigns/service.py`：对话、幂等和状态推进如何串起来。

当前 `CampaignService` 使用内存仓库完成接口闭环，SQLAlchemy 模型和迁移已建立。下一步是在 Docker PostgreSQL 可运行后，将仓库实现替换为异步 PostgreSQL Repository。

