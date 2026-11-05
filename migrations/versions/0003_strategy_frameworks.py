"""add versioned marketing strategy frameworks"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    op.create_table(
        "marketing_strategy_frameworks",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("campaign_id", uuid_type, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_version_id", uuid_type, nullable=True),
        sa.Column("confirmed_version_id", uuid_type, nullable=True),
        sa.Column("hermes_stored_session_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", name="uq_strategy_framework_campaign"),
    )
    op.create_index(
        "ix_marketing_strategy_frameworks_campaign_id",
        "marketing_strategy_frameworks",
        ["campaign_id"],
    )
    op.create_index(
        "ix_marketing_strategy_frameworks_status",
        "marketing_strategy_frameworks",
        ["status"],
    )
    op.create_table(
        "marketing_strategy_versions",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("framework_id", uuid_type, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", uuid_type, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("revision_instruction", sa.Text(), nullable=True),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["framework_id"], ["marketing_strategy_frameworks.id"]
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["marketing_strategy_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "framework_id", "version", name="uq_strategy_framework_version"
        ),
    )
    op.create_index(
        "ix_marketing_strategy_versions_framework_id",
        "marketing_strategy_versions",
        ["framework_id"],
    )
    op.create_index(
        "ix_marketing_strategy_versions_status",
        "marketing_strategy_versions",
        ["status"],
    )
    op.create_foreign_key(
        "fk_strategy_framework_current_version",
        "marketing_strategy_frameworks",
        "marketing_strategy_versions",
        ["current_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_strategy_framework_confirmed_version",
        "marketing_strategy_frameworks",
        "marketing_strategy_versions",
        ["confirmed_version_id"],
        ["id"],
    )
    op.add_column(
        "gate_decisions", sa.Column("resource_type", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "gate_decisions", sa.Column("resource_id", uuid_type, nullable=True)
    )
    op.add_column(
        "gate_decisions", sa.Column("resource_version_id", uuid_type, nullable=True)
    )


def downgrade() -> None:
    op.drop_column("gate_decisions", "resource_version_id")
    op.drop_column("gate_decisions", "resource_id")
    op.drop_column("gate_decisions", "resource_type")
    op.drop_constraint(
        "fk_strategy_framework_confirmed_version",
        "marketing_strategy_frameworks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_strategy_framework_current_version",
        "marketing_strategy_frameworks",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_marketing_strategy_versions_status",
        table_name="marketing_strategy_versions",
    )
    op.drop_index(
        "ix_marketing_strategy_versions_framework_id",
        table_name="marketing_strategy_versions",
    )
    op.drop_table("marketing_strategy_versions")
    op.drop_index(
        "ix_marketing_strategy_frameworks_status",
        table_name="marketing_strategy_frameworks",
    )
    op.drop_index(
        "ix_marketing_strategy_frameworks_campaign_id",
        table_name="marketing_strategy_frameworks",
    )
    op.drop_table("marketing_strategy_frameworks")
