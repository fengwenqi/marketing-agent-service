# Marketing Agent Service

恒智影营销云的 FastAPI 业务服务。当前版本已完成活动上下文采集、A/B/C 策略框架、A 阶段投放计划，以及 15 份视频脚本的批量生成、独立修订和 Gate2 整批确认。

## 本地结构

- FastAPI 在 Windows 的独立 Python 3.12 `.venv` 中运行。
- PostgreSQL 16 由 Docker Desktop 管理。
- Hermes Agent 与 Gateway 使用现有 `marketing-agent` Profile。

三者职责不同：FastAPI 负责 HTTP API、活动状态机和业务编排；PostgreSQL 负责持久化业务数据；Hermes Gateway 负责创建 Agent 会话并执行指定 Skill。Docker Desktop 程序安装在 `D:\Program Files\Docker\Docker`，镜像、容器和数据卷存放在 `D:\DockerData`。

## 项目目录结构

```text
marketing-agent-service/
├─ app/                         # FastAPI 应用源码
│  ├─ campaigns/               # 营销活动、对话、上下文采集和状态机
│  ├─ strategies/              # A/B/C 策略生成、修订、版本与 Gate1
│  ├─ media_plans/             # A 阶段投放计划、版本与 Gate1.5
│  ├─ video_scripts/           # 15 份脚本批任务、修订版本与 Gate2
│  ├─ hermes/                  # Hermes Gateway 适配层和结构化输出契约
│  ├─ gates/                   # Gate 审核规则
│  ├─ models/                  # SQLAlchemy 业务数据模型
│  ├─ database.py              # PostgreSQL 引擎和请求级 Session
│  ├─ settings.py              # 环境变量配置
│  └─ main.py                  # FastAPI 应用入口和路由注册
├─ migrations/                 # Alembic 数据库增量迁移
│  └─ versions/                # 0001～0006 版本迁移脚本
├─ scripts/                    # 本地开发和验证脚本
├─ tests/                      # 单元测试与 PostgreSQL 集成测试
├─ docker-compose.yml          # 本地 PostgreSQL 容器配置
├─ alembic.ini                 # Alembic 配置入口
├─ pyproject.toml              # Python 依赖、测试和 Ruff 配置
├─ uv.lock                     # Python 依赖锁定文件
├─ .env.example                # 可提交的环境变量示例
└─ README.md                   # 项目启动、架构和接口说明
```

### 关键目录与文件说明

| 路径 | 功能说明 |
| --- | --- |
| `app/main.py` | 创建 FastAPI 应用、注入 Hermes 适配器、注册活动和策略路由，并提供健康检查。 |
| `app/settings.py` | 从 `.env` 和系统环境变量读取数据库、Hermes 工作区及超时时间等配置。 |
| `app/database.py` | 创建 SQLAlchemy 异步引擎、Session 工厂和 FastAPI 数据库依赖。 |
| `app/models/business.py` | 定义活动、消息、Gate 决策、Hermes 执行记录、幂等记录及策略版本等 PostgreSQL 模型。 |
| `app/campaigns/domain.py` | 定义活动状态、状态事件和 Gate 枚举等业务概念。 |
| `app/campaigns/state_machine.py` | 校验活动状态流转，阻止跳过业务阶段或执行非法操作。 |
| `app/campaigns/router.py` | 提供创建活动、查询活动、提交消息和读取对话历史的 HTTP API。 |
| `app/campaigns/service.py` | 编排营销目标采集、对话持久化、Hermes 调用、幂等处理和失败恢复。 |
| `app/campaigns/repository.py` | 封装活动、消息、执行记录和状态流转的数据库读写。 |
| `app/campaigns/schemas.py` | 定义活动与消息接口的 Pydantic 请求和响应结构。 |
| `app/strategies/router.py` | 提供策略生成、查询、修订和 Gate1 确认接口，并映射业务错误为 HTTP 状态码。 |
| `app/strategies/service.py` | 编排策略 V1/V2…生成、旧版本留存、失败重试和精确版本确认。 |
| `app/strategies/repository.py` | 封装策略框架、策略版本、Gate 决策及幂等记录的数据库操作。 |
| `app/strategies/schemas.py` | 定义策略修订、确认及完整策略聚合响应的数据结构。 |
| `app/hermes/contracts.py` | 用严格 Pydantic 模型校验营销目标和 A/B/C 策略的 LLM 结构化输出。 |
| `app/hermes/ports.py` | 定义业务层依赖的 Hermes 抽象接口，使业务逻辑不直接依赖 Gateway 实现。 |
| `app/hermes/gateway_adapter.py` | 通过 WebSocket/JSON-RPC 创建或恢复 Hermes 会话，执行对应 Skill 并取得结果。 |
| `app/hermes/result_mapper.py` | 从 Hermes 响应中提取并转换结构化 JSON。 |
| `app/hermes/fake_adapter.py` | 测试专用 Hermes 替身，避免自动化测试真实调用 LLM 或 Gateway。 |
| `app/gates/policy.py` | 定义各 Gate 在不同活动状态下允许执行的审核动作。 |
| `app/video_scripts/executor.py` | 以最大并发 3 执行 15 个脚本作业，并隔离单个槽位失败。 |
| `app/video_scripts/service.py` | 管理脚本批次、单份脚本版本链、失败重试和 Gate2 原子确认。 |
| `app/video_scripts/router.py` | 提供批量生成、查询、重试、单份修订和整批确认接口。 |
| `migrations/versions/` | 保存数据库结构的增量变更；部署或更新后通过 `alembic upgrade head` 应用。 |
| `tests/unit/` | 验证状态机、输出契约、Gateway 适配和 Gate 规则等独立逻辑。 |
| `tests/integration/` | 使用 PostgreSQL 验证持久化、重启恢复、策略版本和完整 API 流程。 |
| `scripts/dev.ps1` | 启动 PostgreSQL、执行迁移并运行 FastAPI 开发服务。 |
| `scripts/verify.ps1` | 执行项目测试和代码质量检查。 |
| `docker-compose.yml` | 定义本地 PostgreSQL 服务、端口、账号和持久化数据卷。 |
| `.env.example` | 环境变量模板；复制为 `.env` 后填写本机配置，真实 `.env` 不提交 Git。 |
| `pyproject.toml` | 管理 Python 版本、项目依赖、pytest 与 Ruff 等工具配置。 |

`.venv/`、`.pytest_cache/`、`.ruff_cache/`、`.env` 等均为本地运行环境或缓存，不属于业务源码，不应提交 Git。`docs/superpowers/` 中本次开发使用的内部设计和执行计划也已通过 `.gitignore` 排除。

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

`docker compose up -d db` 负责启动 PostgreSQL；`alembic upgrade head` 负责把数据库结构升级到代码要求的最新版本。Alembic 会按版本顺序创建业务表以及自己的 `alembic_version` 记录；以后只执行尚未应用的增量迁移，不会重复创建已有表。阶段投放计划对应迁移版本为 `0005`，视频脚本批次与版本对应 `0006`。

Alembic 管理的是表、字段、索引、外键和唯一约束等数据库结构，不负责启动数据库，也不负责写入营销活动数据。当前已建立以下表结构：

- `campaigns`
- `conversation_messages`
- `gate_decisions`
- `hermes_executions`
- `state_transitions`
- `idempotency_records`
- `marketing_strategy_frameworks`
- `marketing_strategy_versions`
- `campaign_stage_workflows`
- `stage_media_plans`
- `stage_media_plan_versions`
- `video_script_batches`
- `video_script_jobs`
- `video_scripts`
- `video_script_versions`
- `video_script_confirmation_sets`

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
- `POST /api/v1/campaigns/{campaign_id}/media-plans/{stage_code}/generate`
- `GET /api/v1/campaigns/{campaign_id}/media-plans/{stage_code}`
- `POST /api/v1/campaigns/{campaign_id}/media-plans/{stage_code}/revise`
- `POST /api/v1/campaigns/{campaign_id}/media-plans/{stage_code}/confirm`
- `POST /api/v1/campaigns/{campaign_id}/video-script-batches/{stage_code}`
- `GET /api/v1/campaigns/{campaign_id}/video-script-batches/{stage_code}`
- `POST /api/v1/campaigns/{campaign_id}/video-script-batches/{stage_code}/retry`
- `POST /api/v1/campaigns/{campaign_id}/video-script-batches/{stage_code}/confirm`
- `GET /api/v1/campaigns/{campaign_id}/video-scripts/{stage_code}`
- `GET /api/v1/campaigns/{campaign_id}/video-scripts/{stage_code}/{script_id}`
- `POST /api/v1/campaigns/{campaign_id}/video-scripts/{stage_code}/{script_id}/revise`

策略接口只允许在合法活动状态下调用。首次生成会创建策略 V1；用户提出修改意见时，Hermes 使用同一策略会话和当前完整版本生成一个新的完整版本，旧版本标记为 `SUPERSEDED` 但不会删除。修订请求本身也记录为 Gate1 的 `REVISION_REQUESTED` 决策。确认时前端必须提交当前 `expected_version`，后端把 `APPROVE` 决策绑定到该版本，避免用户在过期页面误确认旧策略。

```text
CONTEXT_READY
  → 生成策略 V1
STRATEGY_PENDING_CONFIRMATION
  ├─ 提交意见 → V2/V3/... → 继续等待确认
  └─ Gate1 确认当前版本 → STRATEGY_CONFIRMED
```

所有策略写接口也要求 `Idempotency-Key`。Hermes 暂时失败时不会覆盖当前有效版本，同一个幂等键可以安全重试；FastAPI 或 Gateway 重启后，策略版本、Gate 决策和 Hermes 的 `stored_session_id` 都可从 PostgreSQL 恢复。

## A 阶段投放计划与 Gate1.5

Gate1 确认策略后，系统会建立 A/B/C 三条独立阶段工作流。本期只开放 A 阶段投放计划；对 B 或 C 调用计划接口会返回 `409 STAGE_NOT_OPEN`。底层结构已经按阶段隔离，后续开放 B/C 时不需要重新设计版本表和 URL。

```text
Gate1 已确认策略
  → 生成 A 阶段投放计划 V1
MEDIA_PLAN_PENDING_CONFIRMATION
  ├─ 补充关键参数或提交修改意见 → V2/V3/...（旧版本保留）
  └─ 当前版本通过全部硬校验 → A-Gate1.5 确认
MEDIA_PLAN_CONFIRMED
```

计划由 `marketing-media-plan` Skill 生成，拥有独立 Hermes 会话；修订时通过 `stored_session_id` 恢复该计划会话。Hermes 负责受众包、投放节奏、测试矩阵、创意槽位和优化建议，FastAPI 负责以下确定性约束：

- A 阶段预算等于活动总预算的 50%，金额使用十进制计算；
- 确认日期合法，计划日程不超出阶段日期；
- A 阶段必须包含 15 个创意槽位，其中口播 9 个、短视频 6 个；
- 预算单元、受众、日程、测试矩阵和素材槽位引用必须有效；
- Gate1.5 只能确认最新版本，并绑定该版本 ID。

以下字段允许在初始草案中暂缺，但补齐前不能通过 Gate1.5：

- `campaign_total_budget`
- `stage_start_date`
- `stage_end_date`
- `target_roi`

前端可在 `generate` 请求中提供这些字段，也可以随后通过 `revise` 补充。补参和自然语言修改都会生成完整新版本；不会原地覆盖历史记录。所有写请求必须带 `Idempotency-Key`，修订和确认还必须带 `expected_version`，用于防止重试重复执行和旧页面覆盖新计划。

当前计划仅在本系统内生成、修订、确认和持久化，不会调用千川 API，也不会创建真实广告计划；视频脚本只会在 Gate1.5 确认后生成，当前仍不生成视频成片。

## A 阶段视频脚本与 Gate2

Gate1.5 确认 A 阶段投放计划后，创建脚本批次会固定领取该计划中的 15 个创意槽位。接口立即返回 `202`，进程内执行器以最大并发 3 调用 `marketing-video-script` Skill；前端通过查询批次接口展示进度。单个任务失败不会回滚其余脚本，`retry` 只重跑失败槽位。

每份脚本拥有独立 Hermes 会话和 V1/V2…版本链。修改失败不会覆盖当前版本，同一幂等键可安全重试；修改成功后旧版本标记为 `SUPERSEDED`。Gate2 不逐份确认：前端一次提交 15 个 `script_id + version_id`，后端校验它们仍是全部当前版本，并在一个数据库事务中确认 15 份脚本、写入确认清单与 Gate2 决策。任何一项过期或缺失都会整批拒绝，不会出现部分确认。

```text
MEDIA_PLAN_CONFIRMED
  → 创建 15 个脚本作业 → 后台并发生成
  ├─ 个别失败 → PARTIAL_FAILURE → 只重试失败项
  └─ 全部成功 → VIDEO_SCRIPTS_PENDING_CONFIRMATION
       ├─ 单份修改 → 新版本，继续等待
       └─ Gate2 整批确认 → VIDEO_SCRIPTS_CONFIRMED
```

执行器是当前 FastAPI 单实例内的轻量实现。进程意外停止后，启动生命周期会把遗留的 `RUNNING` 作业转换为可重试状态；生产环境扩展到多个实例或需要任务队列时，应再迁移到专用 worker/队列。

## 代码阅读建议

1. `app/campaigns/state_machine.py`：后端如何阻止跨阶段跳转。
2. `app/hermes/contracts.py`：如何用 Pydantic 把 LLM 输出变成可信数据。
3. `app/hermes/gateway_adapter.py`：FastAPI 如何创建指定 Skill 的 Hermes 会话。
4. `app/campaigns/service.py`：对话、幂等和状态推进如何串起来。
5. `app/strategies/service.py`：策略生成、修订、失败恢复和 Gate1 确认。
6. `app/strategies/router.py`：策略 HTTP 接口及 404/409/502/503 错误映射。
7. `app/media_plans/validation.py`：预算、日期、素材数量和引用关系的硬校验。
8. `app/media_plans/service.py`：阶段计划版本、失败恢复、幂等和 Gate1.5。
9. `app/media_plans/router.py`：阶段计划 API 及稳定业务错误码。
10. `app/video_scripts/executor.py`：持久化批任务和并发执行。
11. `app/video_scripts/service.py`：脚本版本链与 Gate2 原子确认。

