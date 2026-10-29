from app.database import Base


def test_business_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "campaigns",
        "conversation_messages",
        "gate_decisions",
        "hermes_executions",
        "state_transitions",
        "idempotency_records",
    }

