from app.database import Base


def test_business_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "campaigns",
        "conversation_messages",
        "gate_decisions",
        "hermes_executions",
        "state_transitions",
        "idempotency_records",
        "marketing_strategy_frameworks",
        "marketing_strategy_versions",
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


def test_strategy_framework_and_versions_are_registered() -> None:
    frameworks = Base.metadata.tables["marketing_strategy_frameworks"]
    versions = Base.metadata.tables["marketing_strategy_versions"]
    version_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in versions.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert frameworks.c.campaign_id.unique is True
    assert frameworks.c.hermes_stored_session_id.nullable is True
    assert frameworks.c.current_version_id.nullable is True
    assert frameworks.c.confirmed_version_id.nullable is True
    assert ("framework_id", "version") in version_unique_columns
    assert versions.c.content.nullable is False
    assert versions.c.parent_version_id.nullable is True
    assert versions.c.revision_instruction.nullable is True


def test_gate_decision_can_reference_exact_business_version() -> None:
    decisions = Base.metadata.tables["gate_decisions"]

    assert decisions.c.resource_type.nullable is True
    assert decisions.c.resource_id.nullable is True
    assert decisions.c.resource_version_id.nullable is True

