"""salon kind (barber, beauty, cosmetology) and staff Telegram chats for new-booking messages

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('salons', sa.Column('kind', sa.String(length=20), server_default='barber', nullable=False))
    op.add_column('staff', sa.Column('tg_chat_id', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('staff', 'tg_chat_id')
    op.drop_column('salons', 'kind')
