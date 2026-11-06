"""Add stage workflows and versioned media plans.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    json_type = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "campaign_stage_workflows",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("campaign_id", uuid_type, nullable=False),
        sa.Column("stage_code", sa.String(length=1), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id", "stage_code", name="uq_stage_workflow_campaign_stage"
        ),
    )
    op.create_index(
        "ix_campaign_stage_workflows_campaign_id",
        "campaign_stage_workflows",
        ["campaign_id"],
    )
    op.create_index(
        "ix_campaign_stage_workflows_state",
        "campaign_stage_workflows",
        ["state"],
    )

    op.create_table(
        "stage_media_plans",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("campaign_id", uuid_type, nullable=False),
        sa.Column("stage_workflow_id", uuid_type, nullable=False),
        sa.Column("stage_code", sa.String(length=1), nullable=False),
        sa.Column("source_strategy_framework_id", uuid_type, nullable=False),
        sa.Column("source_strategy_version_id", uuid_type, nullable=False),
        sa.Column("current_version_id", uuid_type, nullable=True),
        sa.Column("confirmed_version_id", uuid_type, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("hermes_stored_session_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.ForeignKeyConstraint(
            ["source_strategy_framework_id"], ["marketing_strategy_frameworks.id"]
        ),
        sa.ForeignKeyConstraint(
            ["source_strategy_version_id"], ["marketing_strategy_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["stage_workflow_id"], ["campaign_stage_workflows.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stage_workflow_id"),
        sa.UniqueConstraint(
            "campaign_id", "stage_code", name="uq_media_plan_campaign_stage"
        ),
    )
    op.create_index(
        "ix_stage_media_plans_campaign_id", "stage_media_plans", ["campaign_id"]
    )
    op.create_index("ix_stage_media_plans_status", "stage_media_plans", ["status"])

    op.create_table(
        "stage_media_plan_versions",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("media_plan_id", uuid_type, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", uuid_type, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision_instruction", sa.Text(), nullable=True),
        sa.Column("confirmation_inputs", json_type, nullable=False),
        sa.Column("missing_confirmation_fields", json_type, nullable=False),
        sa.Column("validation_result", json_type, nullable=False),
        sa.Column("content", json_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["media_plan_id"], ["stage_media_plans.id"]),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["stage_media_plan_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_plan_id", "version", name="uq_media_plan_version"
        ),
    )
    op.create_index(
        "ix_stage_media_plan_versions_media_plan_id",
        "stage_media_plan_versions",
        ["media_plan_id"],
    )
    op.create_index(
        "ix_stage_media_plan_versions_status",
        "stage_media_plan_versions",
        ["status"],
    )
    op.create_foreign_key(
        "fk_media_plan_current_version",
        "stage_media_plans",
        "stage_media_plan_versions",
        ["current_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_media_plan_confirmed_version",
        "stage_media_plans",
        "stage_media_plan_versions",
        ["confirmed_version_id"],
        ["id"],
    )

    # Existing Gate1-confirmed campaigns must immediately be able to continue.
    op.execute(
        """
        INSERT INTO campaign_stage_workflows
            (id, campaign_id, stage_code, state, version)
        SELECT
            md5(msf.campaign_id::text || ':' || stages.stage_code)::uuid,
            msf.campaign_id,
            stages.stage_code,
            'NOT_STARTED',
            1
        FROM marketing_strategy_frameworks AS msf
        CROSS JOIN (VALUES ('A'), ('B'), ('C')) AS stages(stage_code)
        WHERE msf.confirmed_version_id IS NOT NULL
        ON CONFLICT (campaign_id, stage_code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_media_plan_confirmed_version", "stage_media_plans", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_media_plan_current_version", "stage_media_plans", type_="foreignkey"
    )
    op.drop_index(
        "ix_stage_media_plan_versions_status",
        table_name="stage_media_plan_versions",
    )
    op.drop_index(
        "ix_stage_media_plan_versions_media_plan_id",
        table_name="stage_media_plan_versions",
    )
    op.drop_table("stage_media_plan_versions")
    op.drop_index("ix_stage_media_plans_status", table_name="stage_media_plans")
    op.drop_index("ix_stage_media_plans_campaign_id", table_name="stage_media_plans")
    op.drop_table("stage_media_plans")
    op.drop_index(
        "ix_campaign_stage_workflows_state", table_name="campaign_stage_workflows"
    )
    op.drop_index(
        "ix_campaign_stage_workflows_campaign_id",
        table_name="campaign_stage_workflows",
    )
    op.drop_table("campaign_stage_workflows")
