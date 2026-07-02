"""Тесты реферальной атрибуции (фича 0003): валидация метки и best-effort запись."""

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import UTM_SOURCE_MAX_LENGTH
from app.db import User, UserAttribution
from app.helpers import get_user_deep_link
from app.schemas import RegistrationAttributionSchema
from app.utils import save_registration_attribution, utc_now


def _make_user(db: Session, suffix: str) -> User:
    user = User(
        display_name=f'User {suffix}',
        email=f'{suffix}@mail.ru',
        firebase_uid=f'fb-{suffix}',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


def _attribution_count(db: Session, user: User) -> int:
    return db.scalars(
        select(func.count(UserAttribution.id)).where(UserAttribution.user_id == user.id)
    ).one()


def test_deep_link_without_ref(db: Session):
    """Без реферера ссылка не несёт метку `ref`."""
    user = _make_user(db, 'a')
    link = get_user_deep_link(user)
    assert f'userId={user.id}' in link
    assert 'ref=' not in link


def test_deep_link_with_ref(db: Session):
    """С реферером ссылка несёт `ref={ref.id}`."""
    user = _make_user(db, 'a')
    ref = _make_user(db, 'b')
    link = get_user_deep_link(user, ref=ref)
    assert f'ref={ref.id}' in link


def test_attribution_none_skipped(db: Session):
    """attribution не передан — строка не пишется."""
    user = _make_user(db, 'a')
    save_registration_attribution(db, user, None)
    assert _attribution_count(db, user) == 0


def test_attribution_pure_organic_skipped(db: Session):
    """Ни реферера, ни канала — чистый органик, строку не создаём."""
    user = _make_user(db, 'a')
    save_registration_attribution(
        db, user, RegistrationAttributionSchema(referrer_id=None, utm_source=None)
    )
    assert _attribution_count(db, user) == 0


def test_attribution_valid_referrer_and_channel(db: Session):
    """Валидный реферер + канал — строка с обоими значениями."""
    user = _make_user(db, 'a')
    referrer = _make_user(db, 'b')
    save_registration_attribution(
        db,
        user,
        RegistrationAttributionSchema(
            referrer_id=str(referrer.id), utm_source='telegram'
        ),
    )
    row = db.scalars(
        select(UserAttribution).where(UserAttribution.user_id == user.id)
    ).one()
    assert row.referrer_id == referrer.id
    assert row.utm_source == 'telegram'


def test_attribution_invalid_uuid_referrer_dropped(db: Session):
    """Синтаксически битый `ref` тихо отбрасывается, канал сохраняется."""
    user = _make_user(db, 'a')
    save_registration_attribution(
        db,
        user,
        RegistrationAttributionSchema(referrer_id='not-a-uuid', utm_source='vk'),
    )
    row = db.scalars(
        select(UserAttribution).where(UserAttribution.user_id == user.id)
    ).one()
    assert row.referrer_id is None
    assert row.utm_source == 'vk'


def test_attribution_nonexistent_referrer_dropped(db: Session):
    """Реф-метка на несуществующего юзера отбрасывается."""
    user = _make_user(db, 'a')
    save_registration_attribution(
        db,
        user,
        RegistrationAttributionSchema(referrer_id=str(uuid4()), utm_source='ad'),
    )
    row = db.scalars(
        select(UserAttribution).where(UserAttribution.user_id == user.id)
    ).one()
    assert row.referrer_id is None


def test_attribution_self_referral_dropped(db: Session):
    """Self-referral (метка на самого себя) игнорируется."""
    user = _make_user(db, 'a')
    save_registration_attribution(
        db,
        user,
        RegistrationAttributionSchema(referrer_id=str(user.id), utm_source='self'),
    )
    row = db.scalars(
        select(UserAttribution).where(UserAttribution.user_id == user.id)
    ).one()
    assert row.referrer_id is None
    assert row.utm_source == 'self'


def test_attribution_utm_truncated(db: Session):
    """Переразмерный канал молча усекается до лимита, без ошибки."""
    user = _make_user(db, 'a')
    save_registration_attribution(
        db, user, RegistrationAttributionSchema(utm_source='x' * 200)
    )
    row = db.scalars(
        select(UserAttribution).where(UserAttribution.user_id == user.id)
    ).one()
    assert row.utm_source == 'x' * UTM_SOURCE_MAX_LENGTH


def test_attribution_empty_channel_normalized_to_null(db: Session):
    """Пустая строка канала нормализуется в NULL (при валидном реферере)."""
    user = _make_user(db, 'a')
    referrer = _make_user(db, 'b')
    save_registration_attribution(
        db,
        user,
        RegistrationAttributionSchema(referrer_id=str(referrer.id), utm_source=''),
    )
    row = db.scalars(
        select(UserAttribution).where(UserAttribution.user_id == user.id)
    ).one()
    assert row.utm_source is None


def test_attribution_best_effort_on_db_error(db: Session):
    """Ошибка записи (дубль по 1:1) не всплывает — best-effort, регистрация цела."""
    user = _make_user(db, 'a')
    referrer = _make_user(db, 'b')
    # первая атрибуция уже есть
    db.add(UserAttribution(user_id=user.id, utm_source='first'))
    db.commit()
    # повторная запись нарушает unique(user_id) — должна тихо проглотиться
    save_registration_attribution(
        db, user, RegistrationAttributionSchema(referrer_id=str(referrer.id))
    )
    assert _attribution_count(db, user) == 1
