"""wish_recommendation.category — рекомендации по категориям (фича 0015)

Категория обязательна и без дефолта: рекомендация вне категории на экране не
показывается. Существующие строки — 50 тестовых товаров Читай-города, залитых
один раз; их сносим здесь же (единственная ссылающаяся хотелка теряет
`recommendation_id`), чтобы не придумывать им категорию. Перед прогоном на
проде — бэкап (`backup_postgres.sh`).

Revision ID: e1a7c3b9d2f4
Revises: adef22e9beb9
Create Date: 2026-09-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1a7c3b9d2f4'
down_revision: str | None = 'adef22e9beb9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

recommendation_category = sa.Enum(
    'beauty',
    'jewelry',
    'gadgets',
    'home',
    'books',
    'hobby',
    'clothes',
    'kids',
    name='recommendationcategory',
)


def upgrade() -> None:
    op.execute(
        'UPDATE wish SET recommendation_id = NULL WHERE recommendation_id IS NOT NULL'
    )
    op.execute('DELETE FROM wish_recommendation')
    # add_column тип сам не создаёт.
    recommendation_category.create(op.get_bind())
    op.add_column(
        'wish_recommendation',
        sa.Column('category', recommendation_category, nullable=False),
    )


def downgrade() -> None:
    op.drop_column('wish_recommendation', 'category')
    recommendation_category.drop(op.get_bind())
