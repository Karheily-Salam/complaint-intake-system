"""separate demo from real conversations, and index the list-ordering columns

Revision ID: b8d31f0a5c42
Revises: a1c7e4b92d10
Create Date: 2026-09-10 12:20:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b8d31f0a5c42'
down_revision: str | None = 'a1c7e4b92d10'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index('ix_conversations_is_demo', ['is_demo'], unique=False)
        # list_recent() orders by updated_at; without an index that is a full
        # scan and sort once the table is no longer small.
        batch_op.create_index('ix_conversations_updated_at', ['updated_at'], unique=False)

    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.create_index('ix_tickets_created_at', ['created_at'], unique=False)

    # Every conversation that exists today was created through the public
    # demo endpoint - the email provider has only ever been 'mock', so no real
    # inbound mail has ever been processed. Marking them demo keeps the
    # public demo populated and is factually correct.
    op.execute(sa.text('UPDATE conversations SET is_demo = 1'))


def downgrade() -> None:
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_index('ix_tickets_created_at')

    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_index('ix_conversations_updated_at')
        batch_op.drop_index('ix_conversations_is_demo')
        batch_op.drop_column('is_demo')
