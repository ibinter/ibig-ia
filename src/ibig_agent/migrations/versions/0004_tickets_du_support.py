"""Tickets du support : questions transmises à un humain par l'agent Support.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29 21:28:03.795273
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('tickets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('contact', sa.String(length=300), nullable=False),
    sa.Column('pole', sa.String(length=40), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('draft', sa.Text(), nullable=False),
    sa.Column('sources', sa.JSON(), nullable=False),
    sa.Column('ref', sa.String(length=500), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('resolved_by', sa.String(length=300), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tickets_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_tickets_status'), ['status'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tickets_status'))
        batch_op.drop_index(batch_op.f('ix_tickets_created_at'))

    op.drop_table('tickets')
