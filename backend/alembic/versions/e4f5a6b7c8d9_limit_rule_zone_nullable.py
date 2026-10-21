"""limit_rules.zone nullable: tables without a zone axis (plan 2026-10-06 G1, step 0.5)

Revision ID: e4f5a6b7c8d9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-06 15:40:00

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("limit_rules", "zone", existing_type=sa.String(length=4), nullable=True)


def downgrade() -> None:
    op.alter_column("limit_rules", "zone", existing_type=sa.String(length=4), nullable=False)
