# Marketing Agent Service

恒智影营销云的 FastAPI 业务服务。当前版本已完成活动上下文采集，以及从营销目标生成 A/B/C 阶段策略框架、按反馈修订、版本留痕和 Gate1 人工确认。

## 本地结构

- FastAPI 在 Windows 的独立 Python 3.12 `.venv` 中运行。
- PostgreSQL 16 由 Docker Desktop 管理。
- Hermes Agent 与 Gateway 使用现有 `marketing-agent` Profile。

三者职责不同：FastAPI 负责 HTTP API、活动状态机和业务编排；PostgreSQL 负责持久化业务数据；Hermes Gateway 负责创建 Agent 会话并执行指定 Skill。Docker Desktop 程序安装在 `D:\Program Files\Docker\Docker`，镜像、容器和数据卷存放在 `D:\DockerData`。

## 首次启动

```powershell
cd D:\my-project\AI\Hermes\marketing-agent-service
Copy-Item .env.example .env
docker desktop start
docker compose up -d db
.\.venv\Scripts\python.exe -m alembic upgrade head
$env:HERMES_HOME = 'D:\my-project\AI\Hermes\data\profiles\marketing-agent'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

打开 `http://127.0.0.1:8000/docs` 使用 Swagger。所有写请求都必须传 `Idempotency-Key`，用于避免前端重试导致重复活动或重复 Agent 执行。

日常开发可按以下顺序启动：

```powershell
docker desktop start
hermes -p marketing-agent gateway stop
hermes -p marketing-agent gateway ensure
hermes -p marketing-agent gateway status
cd D:\my-project\AI\Hermes\marketing-agent-service
.\scripts\dev.ps1
```

`dev.ps1` 会依次启动 PostgreSQL 容器、执行 Alembic 迁移并启动 Uvicorn/FastAPI。首次使用前需从 `.env.example` 复制一份 `.env`。结束开发时在 FastAPI 窗口按 `Ctrl+C`，然后按需执行 `docker compose stop db` 和 `docker desktop stop`；不要随意使用 `docker compose down -v`，它会删除 PostgreSQL 数据卷。

## Hermes 调用方式

当前 FastAPI 通过 `hermes_cli.gateway_client.connect_gateway()` 连接独立的 Hermes Gateway。这里导入的是 Hermes 自身使用的传输客户端，并不是在子进程中执行 CLI 命令。客户端通过 WebSocket + JSON-RPC 调用 `session.create`、`prompt.submit` 等方法，并监听完成事件；创建会话时可以明确指定当前业务阶段需要的 Skill。

整体调用关系如下：

```text
FastAPI 进程
  └─ Gateway 客户端（hermes_cli.gateway_client）
           ↓ WebSocket + JSON-RPC
Hermes Gateway 独立服务端进程
  └─ 接收 session.create、prompt.submit 等请求
           ↓
     创建并管理 AIAgent 会话
           ↓
     调用 LLM、Skill 和 Tools
```

`gateway_client` 位于 FastAPI 进程中，只负责连接、发送 RPC 和接收事件；Hermes Gateway 是独立的服务端进程，负责管理连接、会话和 Agent 执行；`AIAgent` 是 Gateway 进程内部的 Agent 运行实例，不会再启动另一个 Gateway。一个 Gateway 进程可以同时管理多个 Agent 会话。

活动第一次对话调用 `session.create`，并把 Gateway 返回的 `stored_session_id` 写入 PostgreSQL；后续对话调用 `session.resume` 恢复同一 Agent 会话。运行期 `session_id` 只在当前 Gateway 进程中有效，不能当成持久化会话 ID。若 Hermes 的 `state.db` 中已找不到原会话，适配器会创建替代会话，并使用 PostgreSQL 保存的结构化活动上下文和最近消息恢复对话。

如果 Gateway 尚未运行，`connect_gateway()` 默认会通过 `ensure_gateway_runtime()` 尝试启动临时 Gateway。FastAPI 业务层只依赖 `HermesPort`，具体的 Gateway 通信集中封装在 `HermesGatewayAdapter` 中，便于以后替换传输实现而不修改活动状态机和业务服务。

单轮 Agent 等待时间由 `.env` 中的 `HERMES_TURN_TIMEOUT_SECONDS` 控制，开发环境默认 600 秒。该值应覆盖模型限流重试和 RAGFlow 检索耗时；它只限制 FastAPI 等待时间，不会加快模型本身。

## PostgreSQL 与 Alembic

`docker compose up -d db` 负责启动 PostgreSQL；`alembic upgrade head` 负责把数据库结构升级到代码要求的最新版本。首次迁移会创建六张业务表以及 Alembic 自己的 `alembic_version` 版本表；以后只按顺序执行尚未应用的增量迁移，不会重复创建已有表。

Alembic 管理的是表、字段、索引、外键和唯一约束等数据库结构，不负责启动数据库，也不负责写入营销活动数据。当前已建立以下表结构：

- `campaigns`
- `conversation_messages`
- `gate_decisions`
- `hermes_executions`
- `state_transitions`
- `idempotency_records`
- `marketing_strategy_frameworks`
- `marketing_strategy_versions`

PostgreSQL 已是活动业务数据的事实来源。每个 HTTP 请求创建独立的 SQLAlchemy Session，`CampaignService` 通过 `CampaignRepository` 持久化活动、结构化上下文、对话历史、状态流转、Hermes 执行结果和幂等结果。重启 FastAPI 后数据不会丢失，前端可重新读取活动并继续原来的 Hermes 会话。

用户消息会在调用 Hermes 前先提交，因此 Gateway 超时或输出无效时，用户原话仍然保留，活动状态会恢复到调用前状态；使用相同 `Idempotency-Key` 可安全重试。成功响应也会按幂等键保存，网络重试不会再次调用 Hermes。PostgreSQL 需要按生产要求备份；Hermes `state.db` 丢失不会丢业务数据，但后续对话会通过 PostgreSQL 内容重建 Agent 上下文。

### 使用 DBeaver 查看 PostgreSQL

本机已安装 DBeaver Community 26.2.0，可执行文件位于 `D:\Program Files\DBeaver\dbeaver.exe`。DBeaver 只是数据库客户端，PostgreSQL 服务仍然运行在 Docker 容器中，不需要在 Windows 上另外安装 PostgreSQL Server。

查看数据库前先确认 Docker Desktop 和 PostgreSQL 容器已经运行：

```powershell
cd D:\my-project\AI\Hermes\marketing-agent-service
docker desktop start
docker compose up -d db
docker compose ps
```

在 DBeaver 中新建 PostgreSQL 连接并填写以下开发环境参数：

```text
Host: 127.0.0.1
Port: 5432
Database: marketing_agent
Username: marketing_agent
Password: marketing_agent_dev
```

以上账号和密码仅用于本地开发，来源于 `.env.example` 和 `docker-compose.yml`。测试环境与生产环境应使用各自的数据库账号和安全保存的密码，不能照搬本地开发凭据。

## 当前 API

- `GET /health`
- `POST /api/v1/campaigns`
- `GET /api/v1/campaigns/{campaign_id}`
- `GET /api/v1/campaigns/{campaign_id}/messages`
- `POST /api/v1/campaigns/{campaign_id}/messages`
- `POST /api/v1/campaigns/{campaign_id}/strategy-framework/generate`
- `GET /api/v1/campaigns/{campaign_id}/strategy-framework`
- `POST /api/v1/campaigns/{campaign_id}/strategy-framework/revise`
- `POST /api/v1/campaigns/{campaign_id}/strategy-framework/confirm`

策略接口只允许在合法活动状态下调用。首次生成会创建策略 V1；用户提出修改意见时，Hermes 使用同一策略会话和当前完整版本生成一个新的完整版本，旧版本标记为 `SUPERSEDED` 但不会删除。修订请求本身也记录为 Gate1 的 `REVISION_REQUESTED` 决策。确认时前端必须提交当前 `expected_version`，后端把 `APPROVE` 决策绑定到该版本，避免用户在过期页面误确认旧策略。

```text
CONTEXT_READY
  → 生成策略 V1
STRATEGY_PENDING_CONFIRMATION
  ├─ 提交意见 → V2/V3/... → 继续等待确认
  └─ Gate1 确认当前版本 → STRATEGY_CONFIRMED
```

所有策略写接口也要求 `Idempotency-Key`。Hermes 暂时失败时不会覆盖当前有效版本，同一个幂等键可以安全重试；FastAPI 或 Gateway 重启后，策略版本、Gate 决策和 Hermes 的 `stored_session_id` 都可从 PostgreSQL 恢复。

## 代码阅读建议

1. `app/campaigns/state_machine.py`：后端如何阻止跨阶段跳转。
2. `app/hermes/contracts.py`：如何用 Pydantic 把 LLM 输出变成可信数据。
3. `app/hermes/gateway_adapter.py`：FastAPI 如何创建指定 Skill 的 Hermes 会话。
4. `app/campaigns/service.py`：对话、幂等和状态推进如何串起来。
5. `app/strategies/service.py`：策略生成、修订、失败恢复和 Gate1 确认。
6. `app/strategies/router.py`：策略 HTTP 接口及 404/409/502/503 错误映射。

