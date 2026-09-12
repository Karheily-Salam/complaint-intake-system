"""add complaint embeddings

Revision ID: d545e396abe4
Revises: 4890d9a405cc
Create Date: 2026-09-12 00:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd545e396abe4'
down_revision: str | None = '4890d9a405cc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No backfill here: the ML worker embeds existing complaints in the
    # background after startup. (Autogenerate's proposal to drop the
    # hand-made ordering indexes from b8d31f0a5c42 was removed.)
    op.create_table('complaint_embeddings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('complaint_id', sa.Integer(), nullable=False),
    sa.Column('model_name', sa.String(length=80), nullable=False),
    sa.Column('model_version', sa.String(length=80), nullable=False),
    sa.Column('dim', sa.Integer(), nullable=False),
    sa.Column('vector', sa.LargeBinary(), nullable=False),
    sa.Column('text_sha256', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['complaint_id'], ['complaints.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('complaint_id', 'model_version', name='uq_complaint_embedding_version')
    )
    with op.batch_alter_table('complaint_embeddings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_complaint_embeddings_complaint_id'), ['complaint_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_complaint_embeddings_model_version'), ['model_version'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('complaint_embeddings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_complaint_embeddings_model_version'))
        batch_op.drop_index(batch_op.f('ix_complaint_embeddings_complaint_id'))

    op.drop_table('complaint_embeddings')
