"""add email threading columns (message.external_message_id, conversation.thread_token)

Revision ID: a1c7e4b92d10
Revises: feb4f7967417
Create Date: 2026-09-10 09:10:00.000000

"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a1c7e4b92d10'
down_revision: str | None = 'feb4f7967417'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_message_id', sa.String(length=500), nullable=True))
        batch_op.create_index(
            'ix_messages_external_message_id', ['external_message_id'], unique=True
        )

    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('thread_token', sa.String(length=32), nullable=True))
        batch_op.create_index('ix_conversations_thread_token', ['thread_token'], unique=True)

    # Backfill existing conversations so pre-existing threads also get a
    # subject-line reference once they next receive a reply.
    conn = op.get_bind()
    ids = [row[0] for row in conn.execute(sa.text('SELECT id FROM conversations'))]
    for conversation_id in ids:
        conn.execute(
            sa.text('UPDATE conversations SET thread_token = :token WHERE id = :id'),
            {'token': uuid.uuid4().hex[:16], 'id': conversation_id},
        )


def downgrade() -> None:
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_index('ix_conversations_thread_token')
        batch_op.drop_column('thread_token')

    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index('ix_messages_external_message_id')
        batch_op.drop_column('external_message_id')
