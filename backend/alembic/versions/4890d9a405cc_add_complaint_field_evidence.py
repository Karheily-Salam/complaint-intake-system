"""add complaint field evidence

Revision ID: 4890d9a405cc
Revises: 729534867a8d
Create Date: 2026-09-12 00:21:06.387683

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '4890d9a405cc'
down_revision: str | None = '729534867a8d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows keep null evidence: it cannot be reconstructed honestly
    # after the fact, and the dashboard simply shows no source for them.
    # (Autogenerate's proposal to drop the hand-made ordering indexes from
    # b8d31f0a5c42 was removed, as in 729534867a8d.)
    with op.batch_alter_table('complaint_fields', schema=None) as batch_op:
        batch_op.add_column(sa.Column('evidence_text', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('evidence_start', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('evidence_end', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('evidence_method', sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('complaint_fields', schema=None) as batch_op:
        batch_op.drop_column('evidence_method')
        batch_op.drop_column('evidence_end')
        batch_op.drop_column('evidence_start')
        batch_op.drop_column('evidence_text')
