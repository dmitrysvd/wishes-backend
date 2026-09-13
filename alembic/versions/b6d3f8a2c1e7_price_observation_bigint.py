"""wish_price_observation.sku / size_option_id: INTEGER → BIGINT

WB-идентификаторы — растущие счётчики: `sizes[].optionId` уже перевалил за
int32 (2 220 626 238 > 2 147 483 647), вставка наблюдения падала с
NumericValueOutOfRange и валила POST /wishes. `nm` (sku) пока ~1,5 млрд, но
это тот же тип счётчика — расширяем оба.

Revision ID: b6d3f8a2c1e7
Revises: a9d2e7f3c5b1
Create Date: 2026-09-13 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b6d3f8a2c1e7'
down_revision: str | None = 'a9d2e7f3c5b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'wish_price_observation',
        'sku',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.alter_column(
        'wish_price_observation',
        'size_option_id',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Упадёт, если в таблице уже есть значения > int32 — это ожидаемо.
    op.alter_column(
        'wish_price_observation',
        'size_option_id',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
    op.alter_column(
        'wish_price_observation',
        'sku',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
