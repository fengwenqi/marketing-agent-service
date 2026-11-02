# Persistent Campaign Conversations Design

## Intent

Marketing campaigns must survive FastAPI restarts, reopen their prior conversation by campaign ID,
and continue the same Hermes conversation across multiple user turns. PostgreSQL is the durable
business source of truth; Hermes sessions provide natural LLM continuity but are recoverable cache-like
runtime state rather than the only copy of user-visible history.

## Goals

- Store campaigns, conversation messages, structured context, state transitions, Hermes executions,
  and idempotency responses in PostgreSQL instead of process memory.
- Expose campaign details and ordered conversation history by campaign ID.
- Reuse a campaign's Hermes stored session for later turns.
- If the Hermes stored session cannot be resumed, create a replacement session and rebuild enough
  context from PostgreSQL to continue safely.
- Preserve the current state-machine and idempotency behavior.

## Non-goals

- User authentication and authorization are outside this phase.
- The browser conversation page itself is outside this FastAPI repository; this phase supplies its API.
- PostgreSQL does not mirror every internal Hermes tool event or reasoning token.
- No production backup scheduler is installed in this phase.

## Constraints

- PostgreSQL remains the system of record for UI history and marketing business state.
- `state.db` remains owned by Hermes; the service does not read or modify it directly.
- Store Hermes `stored_session_id`, never rely on an ephemeral Gateway runtime `session_id` across restarts.
- A failed Gateway call must retain the user message, restore the campaign to its prior retryable state,
  and record the failed Hermes execution.
- The existing API remains compatible where possible.

## Approaches considered

### Selected: PostgreSQL authority plus resumable Hermes session

Store all page-visible messages and business state in PostgreSQL. Store one current
`hermes_stored_session_id` on each campaign. Resume it for later turns. If resume fails because the
session is missing, create a new Hermes session and send the PostgreSQL structured context plus recent
conversation history with the new user message.

This keeps the UI and recovery independent of `state.db`, while preserving natural Hermes continuity
when the session exists.

### Rejected: Hermes-only conversation history

This avoids duplicated history, but deletion, pruning, corruption, or loss of `state.db` would remove
the application's only conversation record.

### Rejected: new Hermes session on every turn

This is recoverable from PostgreSQL but repeatedly retransmits growing history and discards Hermes'
native session semantics.

## Data design

Add `hermes_stored_session_id` to `campaigns`. Add an ordered `sequence` to
`conversation_messages`, unique within a campaign. The service locks the campaign row while allocating
the next sequence so concurrent submissions cannot reorder messages.

Use the existing tables as follows:

- `campaigns`: title, state, latest structured context, version, current stored Hermes session ID.
- `conversation_messages`: ordered user and assistant-visible messages. Assistant rows include the
  complete structured result in `payload`.
- `hermes_executions`: one row per request with skill, stored session ID, input/output payload, status,
  and error.
- `state_transitions`: every accepted state change.
- `idempotency_records`: durable create/message responses keyed by operation scope and caller key.
- `gate_decisions`: unchanged for later phases.

Alembic migration `0002` adds the campaign session ID and message sequence, backfills existing rows,
and creates the per-campaign sequence uniqueness constraint.

## Application flow

### Create campaign

1. Look up the durable idempotency record.
2. Insert the campaign in `DRAFT` when absent.
3. Store and return the response in the same transaction.

### Submit message

1. Lock and load the campaign; return the prior durable response when the idempotency key was used.
2. Apply `MESSAGE_RECEIVED`, allocate a sequence, persist the user message and transition, then commit
   before calling Hermes.
3. Call the Hermes adapter with the stored session ID, current structured context, recent PostgreSQL
   messages, and latest user message.
4. The adapter resumes the stored Hermes session when possible. If it is missing, it creates a new one
   and hydrates the prompt with PostgreSQL context and recent history.
5. Validate the complete structured result.
6. In one database transaction, persist the updated session ID, context, assistant message, execution,
   transition, campaign state, and idempotent response.
7. On Hermes failure, record the failed execution and restore the prior campaign state while retaining
   the user message.

Only the bounded recent transcript is sent during recovery; the current structured context is always
included and remains the primary reconstruction input.

## API design

- `POST /api/v1/campaigns`: create a durable campaign.
- `GET /api/v1/campaigns/{campaign_id}`: return campaign metadata and current context.
- `GET /api/v1/campaigns/{campaign_id}/messages`: return ordered, page-ready conversation history.
- `POST /api/v1/campaigns/{campaign_id}/messages`: append a turn and continue the campaign's Hermes
  session.

History items expose message ID, role, content, structured payload, sequence, and creation time.
Assistant `content` uses `next_question` when present, otherwise `next_action`, so the page renders the
actual follow-up that the user should answer.

## Hermes session lifecycle

`session.create` returns a runtime `session_id` and durable `stored_session_id`. The adapter returns the
stored ID to the service for PostgreSQL persistence. On another request or after a Gateway restart,
`session.resume(session_id=stored_session_id)` returns a current runtime ID; `prompt.submit` uses that
runtime ID.

If resume reports the stored session missing, the adapter creates a replacement session. The replacement
stored ID overwrites the stale campaign value only after a successful turn.

## Failure and recovery

- PostgreSQL unavailable: return a service/database error and do not call Hermes.
- Hermes unavailable: return 503, keep the user message, restore the campaign state, and record failure.
- Hermes session missing: transparently create and hydrate a replacement session.
- Invalid Hermes JSON: return 503 and retain diagnostic execution data without corrupting campaign
  context.
- Duplicate request: return the durable prior response without invoking Hermes again.
- FastAPI restart: all state reloads from PostgreSQL.
- `state.db` loss: UI history remains available from PostgreSQL; the next user turn creates and hydrates
  a replacement Hermes session.

## Verification

- Repository tests cover durable creation, retrieval, ordered messages, transitions, executions, and
  idempotency.
- API integration tests restart the FastAPI application between requests and confirm the campaign and
  history remain available.
- Multi-turn tests confirm the second call receives the first stored session ID and existing context.
- Recovery tests simulate an unresumable Hermes session and confirm a replacement stored ID is saved.
- Existing state-machine, contract, and Gateway streaming tests remain green.
