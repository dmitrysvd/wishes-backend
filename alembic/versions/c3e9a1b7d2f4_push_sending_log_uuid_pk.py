"""push_sending_log: integer pk -> uuid

Переводим первичный ключ push_sending_log с integer (sequence) на UUID, как у
остальных таблиц. Это убирает целый класс багов: рассинхрон sequence после
восстановления из дампа (последствие — падение полуденного крона на INSERT).

Таблица — внутренний append-only лог: наружу через API не отдаётся и на её id нет
внешних ключей, поэтому при смене типа существующие id безопасно перегенерируются.

Revision ID: c3e9a1b7d2f4
Revises: 498438a8dc1b
Create Date: 2026-06-29 17:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3e9a1b7d2f4'
down_revision: str | None = '498438a8dc1b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Снимаем дефолт-nextval, меняем тип на uuid с генерацией новых значений,
    # затем удаляем осиротевшую sequence.
    op.execute('ALTER TABLE push_sending_log ALTER COLUMN id DROP DEFAULT')
    op.execute(
        'ALTER TABLE push_sending_log ALTER COLUMN id TYPE uuid USING gen_random_uuid()'
    )
    op.execute('DROP SEQUENCE IF EXISTS push_sending_log_id_seq')


def downgrade() -> None:
    # Возврат к integer-pk с sequence; значения id перегенерируются.
    op.execute('CREATE SEQUENCE push_sending_log_id_seq')
    op.execute(
        'ALTER TABLE push_sending_log '
        "ALTER COLUMN id TYPE integer USING nextval('push_sending_log_id_seq')"
    )
    op.execute(
        'ALTER TABLE push_sending_log '
        "ALTER COLUMN id SET DEFAULT nextval('push_sending_log_id_seq')"
    )
    op.execute('ALTER SEQUENCE push_sending_log_id_seq OWNED BY push_sending_log.id')
    op.execute(
        "SELECT setval('push_sending_log_id_seq', "
        'COALESCE((SELECT max(id) FROM push_sending_log), 1))'
    )
