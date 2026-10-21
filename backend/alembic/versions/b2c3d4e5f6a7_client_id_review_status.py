"""client_id / error / cost on query_logs, client_id on feedback, review_status on
transition_rules and document_relations (Phase 5 admin & monitoring)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-24 14:00:00

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("query_logs", sa.Column("client_id", sa.String(length=36), nullable=True))
    op.add_column("query_logs", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("query_logs", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.create_index("ix_query_logs_client_created", "query_logs", ["client_id", "created_at"])
    op.create_index("ix_query_logs_status", "query_logs", ["status"])
    op.add_column("feedback", sa.Column("client_id", sa.String(length=36), nullable=True))
    op.add_column(
        "transition_rules",
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="pending"),
    )
    op.add_column(
        "document_relations",
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="pending"),
    )


def downgrade() -> None:
    op.drop_column("document_relations", "review_status")
    op.drop_column("transition_rules", "review_status")
    op.drop_column("feedback", "client_id")
    op.drop_index("ix_query_logs_status", table_name="query_logs")
    op.drop_index("ix_query_logs_client_created", table_name="query_logs")
    op.drop_column("query_logs", "cost_usd")
    op.drop_column("query_logs", "error")
    op.drop_column("query_logs", "client_id")
