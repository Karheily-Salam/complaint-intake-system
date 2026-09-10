"""scope customer identity by demo flag

Revision ID: c4e7a91b2f68
Revises: b8d31f0a5c42
Create Date: 2026-09-10 14:05:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c4e7a91b2f68'
down_revision: str | None = 'b8d31f0a5c42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Email alone no longer identifies a customer: the public demo endpoint
    # takes an unverified sender address, so sharing one row between demo and
    # real data let demo input reach a real customer's record.
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.drop_index('ix_customers_email')
        batch_op.create_index('ix_customers_email', ['email'], unique=False)
        batch_op.create_index('ix_customers_is_demo', ['is_demo'], unique=False)
        batch_op.create_unique_constraint('uq_customer_email_scope', ['email', 'is_demo'])

    # Existing rows are all demo: every conversation to date came from the
    # demo endpoint (the email provider has only ever been 'mock'), which the
    # previous revision recorded by setting conversations.is_demo.
    op.execute(sa.text('UPDATE customers SET is_demo = 1'))


def downgrade() -> None:
    # Collapsing back to one row per address would fail if both a demo and a
    # real customer share one, so drop the demo rows first - they are
    # synthetic by definition.
    op.execute(
        sa.text(
            'DELETE FROM customers WHERE is_demo = 1 AND email IN '
            '(SELECT email FROM customers WHERE is_demo = 0)'
        )
    )
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_constraint('uq_customer_email_scope', type_='unique')
        batch_op.drop_index('ix_customers_is_demo')
        batch_op.drop_index('ix_customers_email')
        batch_op.create_index('ix_customers_email', ['email'], unique=True)
        batch_op.drop_column('is_demo')
