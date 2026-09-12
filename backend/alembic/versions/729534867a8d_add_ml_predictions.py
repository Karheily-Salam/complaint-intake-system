"""add ml predictions

Revision ID: 729534867a8d
Revises: c4e7a91b2f68
Create Date: 2026-09-12 00:13:45.964455

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '729534867a8d'
down_revision: str | None = 'c4e7a91b2f68'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Autogenerate also proposed dropping ix_conversations_updated_at and
    # ix_tickets_created_at. Those are the list-ordering indexes created by
    # hand in b8d31f0a5c42 and deliberately absent from the models, so the
    # proposal was removed.
    op.create_table('ml_predictions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task', sa.String(length=30), nullable=False),
    sa.Column('model_name', sa.String(length=80), nullable=False),
    sa.Column('model_version', sa.String(length=80), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=True),
    sa.Column('message_id', sa.Integer(), nullable=True),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('language_code', sa.String(length=10), nullable=True),
    sa.Column('predicted_label', sa.String(length=60), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('abstained', sa.Boolean(), nullable=False),
    sa.Column('final_label', sa.String(length=60), nullable=True),
    sa.Column('decided_by', sa.String(length=20), nullable=True),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ml_predictions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ml_predictions_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ml_predictions_is_demo'), ['is_demo'], unique=False)
        batch_op.create_index(batch_op.f('ix_ml_predictions_model_version'), ['model_version'], unique=False)
        batch_op.create_index(batch_op.f('ix_ml_predictions_task'), ['task'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('ml_predictions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ml_predictions_task'))
        batch_op.drop_index(batch_op.f('ix_ml_predictions_model_version'))
        batch_op.drop_index(batch_op.f('ix_ml_predictions_is_demo'))
        batch_op.drop_index(batch_op.f('ix_ml_predictions_conversation_id'))

    op.drop_table('ml_predictions')
