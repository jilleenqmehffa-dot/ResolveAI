"""Order messages within a conversation and link completed replies.

Revision ID: c4e2a71d90b6
Revises: b8d614c2e905
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e2a71d90b6"
down_revision: str | Sequence[str] | None = "b8d614c2e905"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("next_message_seq", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("messages", sa.Column("seq", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH numbered AS (
            SELECT id, row_number() OVER (
                PARTITION BY conversation_id ORDER BY created_at, id
            ) AS new_seq
            FROM messages
        )
        UPDATE messages AS m SET seq = numbered.new_seq
        FROM numbered WHERE m.id = numbered.id
        """
    )
    op.execute(
        """
        UPDATE conversations AS c SET next_message_seq = COALESCE(
            (SELECT max(m.seq) + 1 FROM messages AS m WHERE m.conversation_id = c.id),
            1
        )
        """
    )
    op.alter_column("messages", "seq", nullable=False)
    op.create_unique_constraint(
        "uq_messages_conversation_id_seq", "messages", ["conversation_id", "seq"]
    )

    op.add_column("agent_runs", sa.Column("response_message_id", sa.UUID()))
    op.create_foreign_key(
        "fk_agent_runs_response_same_conversation",
        "agent_runs",
        "messages",
        ["conversation_id", "response_message_id"],
        ["conversation_id", "id"],
        ondelete="RESTRICT",
    )
    # Legacy replies have no run ID. Link only unique exact-content matches;
    # repeated answers remain unlinked rather than risking a wrong pairing.
    op.execute(
        """
        WITH completed AS (
            SELECT id, conversation_id, result_text,
                   count(*) OVER (
                       PARTITION BY conversation_id, result_text
                   ) AS matches
            FROM agent_runs
            WHERE status = 'SUCCEEDED' AND result_text IS NOT NULL
        ), replies AS (
            SELECT id, conversation_id, content,
                   count(*) OVER (
                       PARTITION BY conversation_id, content
                   ) AS matches
            FROM messages WHERE role = 'ASSISTANT'
        )
        UPDATE agent_runs AS r SET response_message_id = replies.id
        FROM completed JOIN replies
          ON replies.conversation_id = completed.conversation_id
         AND replies.content = completed.result_text
         AND replies.matches = 1 AND completed.matches = 1
        WHERE r.id = completed.id
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_runs_response_same_conversation", "agent_runs", type_="foreignkey"
    )
    op.drop_column("agent_runs", "response_message_id")
    op.drop_constraint("uq_messages_conversation_id_seq", "messages", type_="unique")
    op.drop_column("messages", "seq")
    op.drop_column("conversations", "next_message_seq")
