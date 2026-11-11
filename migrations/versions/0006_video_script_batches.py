"""Add durable video-script batches, versions, and Gate2 manifests.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    json_type = postgresql.JSONB(astext_type=sa.Text())
    timestamp = sa.DateTime(timezone=True)

    op.create_table(
        "video_script_batches",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("campaign_id", uuid_type, nullable=False),
        sa.Column("stage_workflow_id", uuid_type, nullable=False),
        sa.Column("source_media_plan_id", uuid_type, nullable=False),
        sa.Column("source_media_plan_version_id", uuid_type, nullable=False),
        sa.Column("stage_code", sa.String(1), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", timestamp, nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.ForeignKeyConstraint(["stage_workflow_id"], ["campaign_stage_workflows.id"]),
        sa.ForeignKeyConstraint(["source_media_plan_id"], ["stage_media_plans.id"]),
        sa.ForeignKeyConstraint(["source_media_plan_version_id"], ["stage_media_plan_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_media_plan_version_id"),
    )
    for column in (
        "campaign_id",
        "stage_workflow_id",
        "source_media_plan_id",
        "source_media_plan_version_id",
        "status",
    ):
        op.create_index(f"ix_video_script_batches_{column}", "video_script_batches", [column])

    op.create_table(
        "video_script_jobs",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("batch_id", uuid_type, nullable=False),
        sa.Column("creative_slot_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("started_at", timestamp, nullable=True),
        sa.Column("finished_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["video_script_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "creative_slot_id", name="uq_script_job_batch_slot"),
    )
    op.create_index("ix_video_script_jobs_batch_id", "video_script_jobs", ["batch_id"])
    op.create_index("ix_video_script_jobs_status", "video_script_jobs", ["status"])

    op.create_table(
        "video_scripts",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("campaign_id", uuid_type, nullable=False),
        sa.Column("batch_id", uuid_type, nullable=False),
        sa.Column("creative_slot_id", sa.String(100), nullable=False),
        sa.Column("source_media_plan_id", uuid_type, nullable=False),
        sa.Column("source_media_plan_version_id", uuid_type, nullable=False),
        sa.Column("stage_code", sa.String(1), nullable=False),
        sa.Column("current_version_id", uuid_type, nullable=True),
        sa.Column("confirmed_version_id", uuid_type, nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("hermes_stored_session_id", sa.String(128), nullable=True),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["video_script_batches.id"]),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.ForeignKeyConstraint(["source_media_plan_id"], ["stage_media_plans.id"]),
        sa.ForeignKeyConstraint(["source_media_plan_version_id"], ["stage_media_plan_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "creative_slot_id", name="uq_video_script_batch_slot"),
    )
    op.create_index("ix_video_scripts_batch_id", "video_scripts", ["batch_id"])
    op.create_index("ix_video_scripts_campaign_id", "video_scripts", ["campaign_id"])
    op.create_index("ix_video_scripts_status", "video_scripts", ["status"])

    op.create_table(
        "video_script_versions",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("video_script_id", uuid_type, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", uuid_type, nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision_instruction", sa.Text(), nullable=True),
        sa.Column("validation_result", json_type, nullable=False),
        sa.Column("content", json_type, nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["parent_version_id"], ["video_script_versions.id"]),
        sa.ForeignKeyConstraint(["video_script_id"], ["video_scripts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_script_id", "version", name="uq_video_script_version"),
    )
    op.create_index(
        "ix_video_script_versions_video_script_id", "video_script_versions", ["video_script_id"]
    )
    op.create_index("ix_video_script_versions_status", "video_script_versions", ["status"])
    op.create_foreign_key(
        "fk_video_script_current_version",
        "video_scripts",
        "video_script_versions",
        ["current_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_video_script_confirmed_version",
        "video_scripts",
        "video_script_versions",
        ["confirmed_version_id"],
        ["id"],
    )

    op.create_table(
        "video_script_confirmation_sets",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("batch_id", uuid_type, nullable=False),
        sa.Column("manifest", json_type, nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["video_script_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id"),
    )
    op.create_index(
        "ix_video_script_confirmation_sets_batch_id", "video_script_confirmation_sets", ["batch_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_video_script_confirmation_sets_batch_id", table_name="video_script_confirmation_sets"
    )
    op.drop_table("video_script_confirmation_sets")
    op.drop_constraint("fk_video_script_confirmed_version", "video_scripts", type_="foreignkey")
    op.drop_constraint("fk_video_script_current_version", "video_scripts", type_="foreignkey")
    op.drop_index("ix_video_script_versions_status", table_name="video_script_versions")
    op.drop_index("ix_video_script_versions_video_script_id", table_name="video_script_versions")
    op.drop_table("video_script_versions")
    op.drop_index("ix_video_scripts_status", table_name="video_scripts")
    op.drop_index("ix_video_scripts_campaign_id", table_name="video_scripts")
    op.drop_index("ix_video_scripts_batch_id", table_name="video_scripts")
    op.drop_table("video_scripts")
    op.drop_index("ix_video_script_jobs_status", table_name="video_script_jobs")
    op.drop_index("ix_video_script_jobs_batch_id", table_name="video_script_jobs")
    op.drop_table("video_script_jobs")
    for column in (
        "status",
        "source_media_plan_version_id",
        "source_media_plan_id",
        "stage_workflow_id",
        "campaign_id",
    ):
        op.drop_index(f"ix_video_script_batches_{column}", table_name="video_script_batches")
    op.drop_table("video_script_batches")
