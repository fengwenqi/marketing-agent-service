"""persist campaign conversations and Hermes session identity"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("hermes_stored_session_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("sequence", sa.Integer(), nullable=True),
    )
    # 旧数据按创建时间和主键确定稳定顺序，再把新列收紧为必填。
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY campaign_id ORDER BY created_at, id
                   ) AS seq
            FROM conversation_messages
        )
        UPDATE conversation_messages AS message
        SET sequence = ranked.seq
        FROM ranked
        WHERE message.id = ranked.id
        """
    )
    op.alter_column("conversation_messages", "sequence", nullable=False)
    op.create_unique_constraint(
        "uq_message_campaign_sequence",
        "conversation_messages",
        ["campaign_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_message_campaign_sequence", "conversation_messages", type_="unique"
    )
    op.drop_column("conversation_messages", "sequence")
    op.drop_column("campaigns", "hermes_stored_session_id")
