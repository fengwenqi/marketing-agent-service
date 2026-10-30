# Marketing Agent Service

恒智影营销云的 FastAPI 业务服务。当前版本完成活动上下文采集骨架：活动状态机、Gate 模型、结构化 Skill 输出、Hermes Gateway 适配和 PostgreSQL 表结构。

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

`gateway_client` 位于 FastAPI 进程中，只负责连接、发送 RPC 和接收事件；Hermes Gateway 是独立的服务端进程，负责管理连接、会话和 Agent 执行；`AIAgent` 是 Gateway 进程内部的 Agent 运行实例，不会再启动另一个 Gateway。一个 Gateway 进程可以同时管理多个 Agent 会话，每次调用 `session.create` 创建的是新会话，而不是新的 Gateway 进程。

如果 Gateway 尚未运行，`connect_gateway()` 默认会通过 `ensure_gateway_runtime()` 尝试启动临时 Gateway。FastAPI 业务层只依赖 `HermesPort`，具体的 Gateway 通信集中封装在 `HermesGatewayAdapter` 中，便于以后替换传输实现而不修改活动状态机和业务服务。

## PostgreSQL 与 Alembic

`docker compose up -d db` 负责启动 PostgreSQL；`alembic upgrade head` 负责把数据库结构升级到代码要求的最新版本。首次迁移会创建六张业务表以及 Alembic 自己的 `alembic_version` 版本表；以后只按顺序执行尚未应用的增量迁移，不会重复创建已有表。

Alembic 管理的是表、字段、索引、外键和唯一约束等数据库结构，不负责启动数据库，也不负责写入营销活动数据。当前已建立以下表结构：

- `campaigns`
- `conversation_messages`
- `gate_decisions`
- `hermes_executions`
- `state_transitions`
- `idempotency_records`

当前 `CampaignService` 仍使用 Python 内存仓库存储活动和消息，PostgreSQL 表已准备好但尚未接入 API 的实际读写，因此重启 FastAPI 后活动数据会丢失。下一阶段需要实现 SQLAlchemy Repository，并将内存仓库替换为 PostgreSQL 持久化实现。

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
- `POST /api/v1/campaigns/{campaign_id}/messages`

## 代码阅读建议

1. `app/campaigns/state_machine.py`：后端如何阻止跨阶段跳转。
2. `app/hermes/contracts.py`：如何用 Pydantic 把 LLM 输出变成可信数据。
3. `app/hermes/gateway_adapter.py`：FastAPI 如何创建指定 Skill 的 Hermes 会话。
4. `app/campaigns/service.py`：对话、幂等和状态推进如何串起来。

