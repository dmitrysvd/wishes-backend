"""гостевой резерв (0018): гость, лог гостевых резервов, резерв гостем у хотелки

`guest` — гость публичной страницы (узнаётся по куке `token`), после входа
остаётся со ссылкой на аккаунт для конверсии. `guest_reservation_event` — лог
успешных гостевых резервов: лимит по IP и аналитика. `wish.reserved_by_guest_id`
— резерв гостем; резерв держит либо юзер, либо гость (`single_reserver`).
`followsource.guest_reservation` — подписка на владельца при слиянии гостя.

Revision ID: 3abeedd06809
Revises: d8f2b6a4c1e7
Create Date: 2026-09-26 21:00:51.565970

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3abeedd06809'
down_revision: str | None = 'd8f2b6a4c1e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'guest',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('merged_user_id', sa.Uuid(), nullable=True),
        sa.Column('merged_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['merged_user_id'],
            ['user.id'],
            name=op.f('fk_guest_merged_user_id_user'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_guest')),
        sa.UniqueConstraint('token', name=op.f('uq_guest_token')),
    )
    op.create_table(
        'guest_reservation_event',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('guest_id', sa.Uuid(), nullable=True),
        sa.Column('wish_id', sa.Uuid(), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['guest_id'],
            ['guest.id'],
            name=op.f('fk_guest_reservation_event_guest_id_guest'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['wish_id'],
            ['wish.id'],
            name=op.f('fk_guest_reservation_event_wish_id_wish'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_guest_reservation_event')),
    )
    op.create_index(
        op.f('ix_guest_reservation_event_ip'), 'guest_reservation_event', ['ip']
    )
    op.add_column('wish', sa.Column('reserved_by_guest_id', sa.Uuid(), nullable=True))
    op.create_index(
        op.f('ix_wish_reserved_by_guest_id'), 'wish', ['reserved_by_guest_id']
    )
    op.create_foreign_key(
        op.f('fk_wish_reserved_by_guest_id_guest'),
        'wish',
        'guest',
        ['reserved_by_guest_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_check_constraint(
        op.f('ck_wish_single_reserver'),
        'wish',
        'reserved_by_id IS NULL OR reserved_by_guest_id IS NULL',
    )
    # ADD VALUE у enum в Postgres нельзя выполнять внутри транзакции —
    # оборачиваем в autocommit_block.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE followsource ADD VALUE IF NOT EXISTS 'guest_reservation'"
        )


def downgrade() -> None:
    # Значение enum followsource не удаляем: Postgres не умеет DROP VALUE,
    # оставшееся значение безвредно.
    op.drop_constraint(op.f('ck_wish_single_reserver'), 'wish', type_='check')
    op.drop_constraint(
        op.f('fk_wish_reserved_by_guest_id_guest'), 'wish', type_='foreignkey'
    )
    op.drop_index(op.f('ix_wish_reserved_by_guest_id'), table_name='wish')
    op.drop_column('wish', 'reserved_by_guest_id')
    op.drop_index(
        op.f('ix_guest_reservation_event_ip'), table_name='guest_reservation_event'
    )
    op.drop_table('guest_reservation_event')
    op.drop_table('guest')
