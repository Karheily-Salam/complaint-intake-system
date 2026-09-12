"""add ml feedback

Revision ID: 8883faf476b6
Revises: d545e396abe4
Create Date: 2026-09-12 01:10:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '8883faf476b6'
down_revision: str | None = 'd545e396abe4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # (Autogenerate's proposal to drop the hand-made ordering indexes from
    # b8d31f0a5c42 was removed, as in the other ML revisions.)
    op.create_table('ml_feedback',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('ticket_id', sa.Integer(), nullable=False),
    sa.Column('complaint_id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('language_code', sa.String(length=10), nullable=True),
    sa.Column('field_key', sa.String(length=80), nullable=True),
    sa.Column('original_value', sa.Text(), nullable=True),
    sa.Column('corrected_value', sa.Text(), nullable=False),
    sa.Column('original_source', sa.String(length=40), nullable=True),
    sa.Column('original_confidence', sa.Float(), nullable=True),
    sa.Column('model_name', sa.String(length=80), nullable=True),
    sa.Column('model_version', sa.String(length=80), nullable=True),
    sa.Column('prediction_id', sa.Integer(), nullable=True),
    sa.Column('original_evidence', sa.Text(), nullable=True),
    sa.Column('source_message_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('exported', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['complaint_id'], ['complaints.id'], ),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
    sa.ForeignKeyConstraint(['prediction_id'], ['ml_predictions.id'], ),
    sa.ForeignKeyConstraint(['source_message_id'], ['messages.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ml_feedback', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ml_feedback_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ml_feedback_field_key'), ['field_key'], unique=False)
        batch_op.create_index(batch_op.f('ix_ml_feedback_is_demo'), ['is_demo'], unique=False)
        batch_op.create_index(batch_op.f('ix_ml_feedback_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_ml_feedback_model_version'), ['model_version'], unique=False)
        batch_op.create_index(batch_op.f('ix_ml_feedback_ticket_id'), ['ticket_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('ml_feedback', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ml_feedback_ticket_id'))
        batch_op.drop_index(batch_op.f('ix_ml_feedback_model_version'))
        batch_op.drop_index(batch_op.f('ix_ml_feedback_kind'))
        batch_op.drop_index(batch_op.f('ix_ml_feedback_is_demo'))
        batch_op.drop_index(batch_op.f('ix_ml_feedback_field_key'))
        batch_op.drop_index(batch_op.f('ix_ml_feedback_conversation_id'))

    op.drop_table('ml_feedback')
