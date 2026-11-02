# Persistent Campaign Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist campaign business data in PostgreSQL, expose conversation history by campaign ID, and continue or recover a campaign's Hermes session across multiple turns.

**Architecture:** PostgreSQL is the durable source of truth for campaign state and page-visible history. Each campaign stores a Hermes `stored_session_id`; the adapter resumes it for later turns and creates a replacement hydrated from PostgreSQL context when it is unavailable.

**Tech Stack:** FastAPI, SQLAlchemy 2 async ORM, asyncpg, PostgreSQL 16, Alembic, Pydantic 2, Hermes Gateway JSON-RPC, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-persistent-campaign-conversations-design.md`

## Global Constraints

- Preserve the current API routes and state-machine behavior unless the spec explicitly extends them.
- PostgreSQL is authoritative for UI history and structured campaign state.
- Never read or write Hermes `state.db` directly.
- Persist Hermes `stored_session_id`; treat Gateway runtime `session_id` as ephemeral.
- Retain the user's message and restore the prior retryable campaign state when Hermes fails.
- Add concise Chinese comments around transaction boundaries, ID semantics, session recovery, and failure recovery.
- Keep tool/model reasoning out of page-visible history; store only user and final assistant messages.

## Review Focus

- Concurrent messages for one campaign must allocate deterministic unique sequence numbers and avoid state races; cover with repository locking/sequence tests.
- A duplicate idempotency key after FastAPI restart must return the prior response without another Hermes call; cover in API integration tests.
- A missing Hermes stored session must create a replacement without losing PostgreSQL context; cover in adapter/service tests.
- A Hermes failure after the user message commits must restore campaign state while retaining that message and a failed execution; cover in integration tests.
- History pagination/order must remain stable when timestamps match; cover ordering by per-campaign sequence.

---

### Task 1: Extend the persistent schema

**Files:**
- Modify: `app/models/business.py`
- Create: `migrations/versions/0002_persistent_campaign_conversations.py`
- Modify: `tests/unit/test_database_metadata.py`

**Interfaces:**
- Produces: `Campaign.hermes_stored_session_id: str | None`
- Produces: `ConversationMessage.sequence: int` with unique `(campaign_id, sequence)`

- [ ] **Step 1: Write metadata tests** asserting the new campaign column, message sequence column, and uniqueness constraint.
- [ ] **Step 2: Run the metadata tests and verify they fail.**
- [ ] **Step 3: Add model fields and Chinese comments** explaining durable versus runtime Hermes IDs and deterministic message ordering.
- [ ] **Step 4: Add Alembic revision `0002`** that adds nullable fields, backfills existing message sequences per campaign, makes sequence non-null, and creates the unique constraint.
- [ ] **Step 5: Run Alembic against PostgreSQL and inspect the upgraded schema.**
- [ ] **Step 6: Run metadata tests and verify they pass.**

### Task 2: Add PostgreSQL repositories and durable idempotency

**Files:**
- Create: `app/campaigns/repository.py`
- Modify: `app/campaigns/service.py`
- Create: `tests/integration/test_campaign_repository.py`

**Interfaces:**
- Produces: async campaign create/get/lock/update operations.
- Produces: ordered message append/list operations.
- Produces: execution, transition, and idempotency persistence operations.

- [ ] **Step 1: Write repository integration tests** for campaign persistence, ordered history, state transitions, executions, and durable idempotency.
- [ ] **Step 2: Run them and verify failure because the repository does not exist.**
- [ ] **Step 3: Implement `CampaignRepository`** using an injected `AsyncSession`, row locking for message submission, and Chinese comments on concurrency and transaction responsibilities.
- [ ] **Step 4: Replace `CampaignService` memory dictionaries** with repository calls and explicit commit phases around the external Hermes call.
- [ ] **Step 5: Run repository tests and verify they pass.**

### Task 3: Add Hermes stored-session continuation and recovery

**Files:**
- Modify: `app/hermes/ports.py`
- Modify: `app/hermes/gateway_adapter.py`
- Modify: `app/hermes/fake_adapter.py`
- Modify: `tests/unit/test_gateway_adapter.py`
- Create: `tests/unit/test_hermes_session_flow.py`

**Interfaces:**
- Produces: `ContextIntakeRequest` fields for stored session ID, structured context, and recent messages.
- Produces: a response wrapper containing `MarketingContextResult` and the effective `stored_session_id`.
- Consumes: persisted campaign context and history from Task 2.

- [ ] **Step 1: Write failing tests** for create-on-first-turn, resume-on-later-turn, prompt hydration, and replacement-on-missing-session.
- [ ] **Step 2: Extend the Hermes port contracts** so the service receives the effective stored session ID with every successful result.
- [ ] **Step 3: Implement create/resume behavior** in the Gateway adapter, with Chinese comments distinguishing runtime and stored IDs.
- [ ] **Step 4: Implement recovery hydration** that includes the current structured context and bounded recent PostgreSQL messages only when rebuilding a missing session.
- [ ] **Step 5: Update the fake adapter** to record requests and return stored session IDs for service tests.
- [ ] **Step 6: Run Gateway/session unit tests and verify they pass.**

### Task 4: Expose durable campaign and history APIs

**Files:**
- Modify: `app/campaigns/schemas.py`
- Modify: `app/campaigns/router.py`
- Modify: `app/main.py`
- Modify: `tests/integration/test_context_intake_flow.py`

**Interfaces:**
- Produces: `GET /api/v1/campaigns/{campaign_id}` with current context.
- Produces: `GET /api/v1/campaigns/{campaign_id}/messages` with ordered message views.
- Consumes: repository-backed `CampaignService` and request-scoped `AsyncSession`.

- [ ] **Step 1: Rewrite API integration fixtures** to use an isolated PostgreSQL database transaction and repository-backed application.
- [ ] **Step 2: Write failing API tests** for history retrieval, process/app restart durability, and multi-turn session reuse.
- [ ] **Step 3: Inject request-scoped database sessions** into campaign routes and construct services with repositories.
- [ ] **Step 4: Add campaign-context and message-history response schemas** with stable sequence ordering.
- [ ] **Step 5: Add the history endpoint** and Chinese comments describing why page history comes from PostgreSQL rather than Hermes.
- [ ] **Step 6: Run API integration tests and verify they pass.**

### Task 5: Complete failure recovery and regression verification

**Files:**
- Modify: `tests/integration/test_context_intake_flow.py`
- Modify: `README.md`

**Interfaces:**
- Verifies all interfaces produced in Tasks 1–4.

- [ ] **Step 1: Add tests** for duplicate keys after app restart, Gateway failure persistence, invalid structured output, and missing-session replacement.
- [ ] **Step 2: Run the full test suite and fix only failures caused by this implementation.**
- [ ] **Step 3: Run Ruff and Alembic upgrade validation.**
- [ ] **Step 4: Perform a local real-Gateway smoke test** with two turns on one campaign and query the history endpoint.
- [ ] **Step 5: Update README** with startup/migration steps, history API examples, session recovery semantics, and `state.db` retention/backup guidance.
- [ ] **Step 6: Re-run the full test suite and record the final evidence.**

## Self-review

- The plan covers all three requested behaviors and the approved missing-`state.db` fallback.
- PostgreSQL transaction boundaries and the external Hermes call are separated so a long LLM request does not hold a row lock.
- The data types and ID names remain consistent: `hermes_stored_session_id` is durable; `session_id` is runtime-only.
- Stable message ordering does not depend on timestamp precision.
- Chinese comments are required only at decision-heavy boundaries, avoiding comments that merely restate syntax.
