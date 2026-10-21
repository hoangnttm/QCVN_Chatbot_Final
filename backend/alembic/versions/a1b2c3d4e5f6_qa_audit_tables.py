"""qa audit tables: query_logs, answers, answer_citations, feedback (PLAN.md §8, Phase 4)

Revision ID: a1b2c3d4e5f6
Revises: 48f9c8ffe04c
Create Date: 2026-09-24 09:10:00

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "48f9c8ffe04c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("intent", sa.String(length=16), nullable=False),
        sa.Column("understanding", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("retrieved", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("structured_hits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=16), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=True),
        sa.Column("thinking_level", sa.String(length=16), nullable=True),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latency_ms", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_query_logs_created_at", "query_logs", ["created_at"])
    op.create_table(
        "answers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("abstained", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["query_id"], ["query_logs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_answers_query", "answers", ["query_id"])
    op.create_table(
        "answer_citations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("answer_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=288), nullable=False),
        sa.Column("citation_label", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["answer_id"], ["answers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_answer_citations_chunk", "answer_citations", ["chunk_id"])
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["query_id"], ["query_logs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_index("ix_answer_citations_chunk", table_name="answer_citations")
    op.drop_table("answer_citations")
    op.drop_index("ix_answers_query", table_name="answers")
    op.drop_table("answers")
    op.drop_index("ix_query_logs_created_at", table_name="query_logs")
    op.drop_table("query_logs")
