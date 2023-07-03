"""Make nullable for test receipt

Revision ID: c67c061f66e8
Revises: 64dd54c4aef4
Create Date: 2023-07-03 15:25:47.257458

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c67c061f66e8'
down_revision = '64dd54c4aef4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('receipt', 'purchased_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True)
    op.alter_column('receipt', 'product_id',
               existing_type=sa.INTEGER(),
               nullable=True)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('receipt', 'product_id',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.alter_column('receipt', 'purchased_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False)
    # ### end Alembic commands ###
