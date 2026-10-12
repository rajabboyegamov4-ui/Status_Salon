"""staff choose their language (uz | ru) for bot messages and panels; a Telegram login remembers the site's language

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('staff', sa.Column('language', sa.String(length=5), server_default='uz', nullable=False))
    op.add_column('tg_logins', sa.Column('language', sa.String(length=5), server_default='uz', nullable=False))


def downgrade() -> None:
    op.drop_column('tg_logins', 'language')
    op.drop_column('staff', 'language')
