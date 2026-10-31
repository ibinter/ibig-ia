"""Schéma initial : journal, validations, arrêt, mails traités, prospects, comptes, usage IA.

Revision ID: 0001
Revises:
Create Date: 2026-09-29 20:56:23.591182
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('ai_usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('purpose', sa.String(length=80), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_usage_created_at'), ['created_at'], unique=False)

    op.create_table('channel_state',
    sa.Column('channel', sa.String(length=40), nullable=False),
    sa.Column('stopped', sa.Boolean(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.String(length=120), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('channel')
    )
    op.create_table('journal',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('agent', sa.String(length=40), nullable=False),
    sa.Column('action_type', sa.String(length=60), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=40), nullable=False),
    sa.Column('account', sa.String(length=200), nullable=False),
    sa.Column('pole', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('decided_by', sa.String(length=120), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('journal', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_journal_created_at'), ['created_at'], unique=False)

    op.create_table('pending_actions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('agent', sa.String(length=40), nullable=False),
    sa.Column('action_type', sa.String(length=60), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('channel', sa.String(length=40), nullable=False),
    sa.Column('account', sa.String(length=200), nullable=False),
    sa.Column('pole', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('decided_by', sa.String(length=120), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('flagged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('pending_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pending_actions_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_pending_actions_status'), ['status'], unique=False)

    op.create_table('processed_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('mailbox', sa.String(length=200), nullable=False),
    sa.Column('message_id', sa.String(length=500), nullable=False),
    sa.Column('sender', sa.String(length=300), nullable=False),
    sa.Column('subject', sa.Text(), nullable=False),
    sa.Column('pole', sa.String(length=40), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('urgency', sa.String(length=20), nullable=False),
    sa.Column('sentiment', sa.String(length=20), nullable=False),
    sa.Column('decision', sa.String(length=40), nullable=False),
    sa.Column('suspicious', sa.Boolean(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('mailbox', 'message_id')
    )
    with op.batch_alter_table('processed_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_processed_messages_mailbox'), ['mailbox'], unique=False)
        batch_op.create_index(batch_op.f('ix_processed_messages_processed_at'), ['processed_at'], unique=False)

    op.create_table('prospects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=300), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('pole', sa.String(length=40), nullable=False),
    sa.Column('source', sa.String(length=200), nullable=False),
    sa.Column('need', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('prospects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_prospects_email'), ['email'], unique=True)

    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=300), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('password_hash', sa.String(length=300), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    with op.batch_alter_table('prospects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_prospects_email'))

    op.drop_table('prospects')
    with op.batch_alter_table('processed_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_processed_messages_processed_at'))
        batch_op.drop_index(batch_op.f('ix_processed_messages_mailbox'))

    op.drop_table('processed_messages')
    with op.batch_alter_table('pending_actions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pending_actions_status'))
        batch_op.drop_index(batch_op.f('ix_pending_actions_created_at'))

    op.drop_table('pending_actions')
    with op.batch_alter_table('journal', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_journal_created_at'))

    op.drop_table('journal')
    op.drop_table('channel_state')
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_usage_created_at'))

    op.drop_table('ai_usage')
