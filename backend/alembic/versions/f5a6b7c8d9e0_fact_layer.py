"""fact layer: cell address / trust / validity on limit_rules, rule_conditions, facts (plan G2)

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-10-06 17:30:00

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("limit_rules", sa.Column("zone_scheme", sa.String(length=64), nullable=True))
    op.add_column("limit_rules", sa.Column("column_key", sa.String(length=64), nullable=True))
    op.add_column("limit_rules", sa.Column("cell_raw", sa.Text(), nullable=True))
    op.add_column(
        "limit_rules",
        sa.Column(
            "address",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column(
        "limit_rules",
        sa.Column("trust", sa.String(length=16), server_default="pending", nullable=False),
    )
    op.add_column("limit_rules", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("limit_rules", sa.Column("valid_to", sa.Date(), nullable=True))

    op.create_table(
        "rule_conditions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_code", sa.String(length=192), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("op", sa.String(length=8), nullable=True),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("inherited_from", sa.String(length=160), nullable=True),
        sa.Column("scope", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["rule_code"], ["limit_rules.rule_code"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.document_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rule_conditions_rule", "rule_conditions", ["rule_code"])
    op.create_index("ix_rule_conditions_dim", "rule_conditions", ["dimension", "value_text"])

    op.create_table(
        "facts",
        sa.Column("fact_id", sa.String(length=320), nullable=False),
        sa.Column("fact_type", sa.String(length=24), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_id", sa.String(length=288), nullable=True),
        sa.Column("address", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("address_label", sa.Text(), nullable=True),
        sa.Column("locator", sa.Text(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("trust", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("ref_table", sa.String(length=32), nullable=False),
        sa.Column("ref_id", sa.String(length=320), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.document_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fact_id"),
    )
    op.create_index("ix_facts_doc_type", "facts", ["document_id", "fact_type"])
    op.create_index("ix_facts_chunk", "facts", ["chunk_id"])


def downgrade() -> None:
    op.drop_index("ix_facts_chunk", table_name="facts")
    op.drop_index("ix_facts_doc_type", table_name="facts")
    op.drop_table("facts")
    op.drop_index("ix_rule_conditions_dim", table_name="rule_conditions")
    op.drop_index("ix_rule_conditions_rule", table_name="rule_conditions")
    op.drop_table("rule_conditions")
    for column in (
        "valid_to",
        "valid_from",
        "trust",
        "address",
        "cell_raw",
        "column_key",
        "zone_scheme",
    ):
        op.drop_column("limit_rules", column)
