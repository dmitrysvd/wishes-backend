"""add wish_recommendation

Revision ID: 7b0c3a26e25e
Revises: 71c61be4c32e
Create Date: 2026-04-08 19:38:55.089289

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '7b0c3a26e25e'
down_revision: str | None = '71c61be4c32e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'wish_recommendation',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=250), nullable=False),
        sa.Column('description', sa.String(length=1000), nullable=True),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('link', sa.String(length=500), nullable=False),
        sa.Column('image_url', sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('wish', schema=None) as batch_op:
        batch_op.add_column(sa.Column('recommendation_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            'fk_wish_recommendation_id',
            'wish_recommendation',
            ['recommendation_id'],
            ['id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('wish', schema=None) as batch_op:
        batch_op.drop_constraint('fk_wish_recommendation_id', type_='foreignkey')
        batch_op.drop_column('recommendation_id')
    op.drop_table('wish_recommendation')
