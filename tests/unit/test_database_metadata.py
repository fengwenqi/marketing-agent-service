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
        "campaign_stage_workflows",
        "stage_media_plans",
        "stage_media_plan_versions",
        "video_script_batches",
        "video_script_jobs",
        "video_scripts",
        "video_script_versions",
        "video_script_confirmation_sets",
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


def test_stage_workflows_and_media_plan_versions_are_constrained() -> None:
    workflows = Base.metadata.tables["campaign_stage_workflows"]
    plans = Base.metadata.tables["stage_media_plans"]
    versions = Base.metadata.tables["stage_media_plan_versions"]

    workflow_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in workflows.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    plan_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in plans.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    version_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in versions.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("campaign_id", "stage_code") in workflow_uniques
    assert ("campaign_id", "stage_code") in plan_uniques
    assert ("media_plan_id", "version") in version_uniques
    assert plans.c.source_strategy_version_id.nullable is False
    assert plans.c.current_version_id.nullable is True
    assert plans.c.confirmed_version_id.nullable is True
    assert versions.c.confirmation_inputs.nullable is False
    assert versions.c.missing_confirmation_fields.nullable is False
    assert versions.c.validation_result.nullable is False
    assert versions.c.content.nullable is False


def test_video_script_batch_and_versions_are_constrained() -> None:
    batches = Base.metadata.tables["video_script_batches"]
    jobs = Base.metadata.tables["video_script_jobs"]
    scripts = Base.metadata.tables["video_scripts"]
    versions = Base.metadata.tables["video_script_versions"]
    confirmation_sets = Base.metadata.tables["video_script_confirmation_sets"]

    def uniques(table):
        return {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }

    assert ("source_media_plan_version_id",) in uniques(batches)
    assert ("batch_id", "creative_slot_id") in uniques(jobs)
    assert ("batch_id", "creative_slot_id") in uniques(scripts)
    assert ("video_script_id", "version") in uniques(versions)
    assert ("batch_id",) in uniques(confirmation_sets)
    assert scripts.c.current_version_id.nullable is True
    assert scripts.c.confirmed_version_id.nullable is True
    assert versions.c.content.nullable is False
    assert versions.c.validation_result.nullable is False
    assert confirmation_sets.c.manifest.nullable is False
    assert confirmation_sets.c.content_digest.nullable is False
