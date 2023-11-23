"""Update fungible_asset_product to use claim action

Revision ID: 73e03cbf8ba2
Revises: e75f93e4cc06
Create Date: 2023-11-18 01:38:51.711431

"""
from sqlalchemy import Enum

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '73e03cbf8ba2'
down_revision = 'e75f93e4cc06'
branch_labels = None
depends_on = None

enum = Enum("NCG", "CRYSTAL", "GARAGE", name='currency')


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('fungible_asset_product', sa.Column('decimal_places', sa.Integer(), nullable=True))
    op.execute("UPDATE fungible_asset_product SET decimal_places=18")
    op.alter_column("fungible_asset_product", "decimal_places", nullable=False)
    op.alter_column('fungible_asset_product', 'ticker',
               existing_type=enum,
               type_=sa.Text(),
               existing_nullable=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    print("New ticker tan be added to table which is not in the `Currency` Enum.")
    print("This command will not modify `ticker` column.")
    print("To downgrade, do this:")
    print("1. Check and change all the values to meet `Currency` Enum value. (Do not modify `Currency` Enum)")
    print("2. Update the column type from text to `Currency` Enum")
    # op.alter_column('fungible_asset_product', 'ticker',
    #            existing_type=sa.Text(),
    #            type_=enum,
    #            existing_nullable=False)
    op.drop_column('fungible_asset_product', 'decimal_places')
    # ### end Alembic commands ###
