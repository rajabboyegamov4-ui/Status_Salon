"""the platform's own team (owner, partner, sales), leads, payments; demo salons

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('salons', sa.Column('is_demo', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_table(
        'platform_users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('login', sa.String(length=40), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(length=200), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'partner', 'sales')", name='platform_users_role_check'),
    )
    op.create_table(
        'leads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False, server_default='other'),
        sa.Column('phone', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('contact', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('address', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='new'),
        sa.Column('assigned_to', sa.Integer(), sa.ForeignKey('platform_users.id'), nullable=True),
        sa.Column('salon_id', sa.Integer(), sa.ForeignKey('salons.id'), nullable=True, unique=True),
        sa.Column('next_step_on', sa.Date(), nullable=True),
        sa.Column('next_step', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('platform_users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('new', 'talked', 'demo', 'trial', 'paid', 'lost')", name='leads_status_check'),
    )
    op.create_index('ix_leads_assigned_to', 'leads', ['assigned_to'])
    op.create_table(
        'payments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('salon_id', sa.Integer(), sa.ForeignKey('salons.id'), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('months', sa.Integer(), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('platform_users.id'), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_payments_salon_id', 'payments', ['salon_id'])


def downgrade() -> None:
    op.drop_table('payments')
    op.drop_table('leads')
    op.drop_table('platform_users')
    op.drop_column('salons', 'is_demo')
