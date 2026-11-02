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


def test_campaign_persists_durable_hermes_session_id() -> None:
    campaigns = Base.metadata.tables["campaigns"]

    assert "hermes_stored_session_id" in campaigns.c
    assert campaigns.c.hermes_stored_session_id.nullable is True


def test_conversation_messages_have_stable_campaign_sequence() -> None:
    messages = Base.metadata.tables["conversation_messages"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in messages.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert "sequence" in messages.c
    assert messages.c.sequence.nullable is False
    assert ("campaign_id", "sequence") in unique_columns

