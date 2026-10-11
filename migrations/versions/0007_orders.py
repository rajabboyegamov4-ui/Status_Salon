"""orders module: products, orders, order items; which modules a business uses; product photos

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('salons', sa.Column('modules', sa.String(length=40), server_default='booking', nullable=False))
    op.add_column('salons', sa.Column('delivery', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('salons', sa.Column('pickup', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('salons', sa.Column('delivery_fee', sa.Integer(), server_default='0', nullable=False))
    op.add_column('salons', sa.Column('min_order', sa.Integer(), server_default='0', nullable=False))
    op.drop_constraint('photos_kind_check', 'photos', type_='check')
    op.create_check_constraint('photos_kind_check', 'photos', "kind IN ('cover', 'logo', 'branch', 'master', 'work', 'product')")
    op.create_table(
        'products',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('salon_id', sa.Integer(), sa.ForeignKey('salons.id'), nullable=False),
        sa.Column('category', sa.String(length=60), nullable=False, server_default=''),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('price', sa.BigInteger(), nullable=False),
        sa.Column('photo_url', sa.String(length=80), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('sort', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_products_salon_id', 'products', ['salon_id'])
    op.create_table(
        'orders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('salon_id', sa.Integer(), sa.ForeignKey('salons.id'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='new'),
        sa.Column('fulfillment', sa.String(length=10), nullable=False, server_default='pickup'),
        sa.Column('address', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('comment', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('items_total', sa.BigInteger(), nullable=False),
        sa.Column('delivery_fee', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total', sa.BigInteger(), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('salon_id', 'number', name='orders_salon_number'),
        sa.UniqueConstraint('salon_id', 'idempotency_key', name='orders_salon_idempotency'),
        sa.CheckConstraint("status IN ('new', 'accepted', 'ready', 'delivering', 'done', 'cancelled')", name='orders_status_check'),
        sa.CheckConstraint("fulfillment IN ('pickup', 'delivery')", name='orders_fulfillment_check'),
    )
    op.create_index('ix_orders_salon_id', 'orders', ['salon_id'])
    op.create_index('ix_orders_user_id', 'orders', ['user_id'])
    op.create_table(
        'order_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('orders.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id'), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('price', sa.BigInteger(), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
    )
    op.create_index('ix_order_items_order_id', 'order_items', ['order_id'])


def downgrade() -> None:
    op.drop_table('order_items')
    op.drop_table('orders')
    op.drop_table('products')
    op.drop_constraint('photos_kind_check', 'photos', type_='check')
    op.create_check_constraint('photos_kind_check', 'photos', "kind IN ('cover', 'logo', 'branch', 'master', 'work')")
    for col in ('min_order', 'delivery_fee', 'pickup', 'delivery', 'modules'):
        op.drop_column('salons', col)
