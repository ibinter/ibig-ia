"""Contacts WhatsApp : fenêtre de 24 h, désinscription et consentement.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29 21:42:13.455444
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('whatsapp_contacts',
    sa.Column('wa_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('phone_number_id', sa.String(length=40), nullable=False),
    sa.Column('pole', sa.String(length=40), nullable=False),
    sa.Column('last_inbound_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_ack_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('opted_out', sa.Boolean(), nullable=False),
    sa.Column('marketing_opt_in', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('wa_id')
    )


def downgrade() -> None:
    op.drop_table('whatsapp_contacts')
