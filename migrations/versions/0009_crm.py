"""the team's CRM: more about each lead, conversations, a "later" status, the money goal

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, col in (
        ('district', sa.String(length=80)), ('size', sa.String(length=10)), ('source', sa.String(length=40)),
        ('how_now', sa.String(length=40)), ('lost_reason', sa.String(length=200)),
    ):
        op.add_column('leads', sa.Column(name, col, server_default='', nullable=False))
    op.add_column('leads', sa.Column('staff_count', sa.SmallInteger(), nullable=True))
    op.drop_constraint('leads_status_check', 'leads', type_='check')
    op.create_check_constraint('leads_status_check', 'leads', "status IN ('new', 'talked', 'demo', 'trial', 'paid', 'lost', 'later')")
    op.create_table(
        'lead_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('platform_users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('call', 'meeting', 'message', 'demo')", name='lead_notes_kind_check'),
    )
    op.create_index('ix_lead_notes_lead_id', 'lead_notes', ['lead_id'])
    op.create_table(
        'platform_settings',
        sa.Column('key', sa.String(length=40), primary_key=True),
        sa.Column('value', sa.String(length=200), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('platform_settings')
    op.drop_index('ix_lead_notes_lead_id', 'lead_notes')
    op.drop_table('lead_notes')
    op.execute("UPDATE leads SET status = 'new' WHERE status = 'later'")
    op.drop_constraint('leads_status_check', 'leads', type_='check')
    op.create_check_constraint('leads_status_check', 'leads', "status IN ('new', 'talked', 'demo', 'trial', 'paid', 'lost')")
    for name in ('staff_count', 'lost_reason', 'how_now', 'source', 'size', 'district'):
        op.drop_column('leads', name)
