"""telegram bot per salon instead of SMS: bot token, client chat ids, login attempts

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('salons', sa.Column('tg_token_sealed', sa.Text(), nullable=True))
    op.add_column('salons', sa.Column('tg_bot_username', sa.String(length=64), server_default='', nullable=False))
    op.add_column('salons', sa.Column('tg_webhook_secret', sa.String(length=64), server_default='', nullable=False))
    op.add_column('users', sa.Column('tg_chat_id', sa.BigInteger(), nullable=True))
    op.create_index('users_salon_tg_chat', 'users', ['salon_id', 'tg_chat_id'])
    op.create_table(
        'tg_logins',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('salon_id', sa.Integer(), sa.ForeignKey('salons.id'), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False, unique=True),
        sa.Column('chat_id', sa.BigInteger(), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_tg_logins_salon_id', 'tg_logins', ['salon_id'])


def downgrade() -> None:
    op.drop_table('tg_logins')
    op.drop_index('users_salon_tg_chat', table_name='users')
    op.drop_column('users', 'tg_chat_id')
    op.drop_column('salons', 'tg_webhook_secret')
    op.drop_column('salons', 'tg_bot_username')
    op.drop_column('salons', 'tg_token_sealed')
