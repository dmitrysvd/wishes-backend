import os
import runpy

from sqlalchemy.orm import Session

from app.cron_scripts import backfill_vk_friends
from app.cron_scripts.backfill_vk_friends import main
from app.db import User
from app.utils import utc_now


def _make_user(db: Session, suffix: str, **kwargs) -> User:
    user = User(
        display_name=f'U{suffix}',
        firebase_uid=f'fb_{suffix}',
        registered_at=utc_now(),
        **kwargs,
    )
    db.add(user)
    db.commit()
    return user


def test_main_refreshes_snapshot(db: Session):
    user = _make_user(
        db, 'live', vk_access_token='tok', vk_friends_data=[{'id': 'old'}]
    )
    fresh = [{'id': 1, 'bdate': '1.1', 'photo_100': 'https://vk/p.jpg'}]

    main(fetch_friends=lambda _token: fresh, delay_seconds=0)

    db.refresh(user)
    assert user.vk_friends_data == fresh


def test_main_keeps_snapshot_when_token_dead(db: Session):
    user = _make_user(
        db, 'dead', vk_access_token='dead', vk_friends_data=[{'id': 'keep'}]
    )

    def _raise(_token):
        raise Exception('token expired')

    # Сбой не роняет прогон, снимок остаётся прежним.
    main(fetch_friends=_raise, delay_seconds=0)

    db.refresh(user)
    assert user.vk_friends_data == [{'id': 'keep'}]


def test_main_skips_users_without_token(db: Session):
    user = _make_user(db, 'notok', vk_friends_data=[{'id': 'x'}])

    def _fail(_token):
        raise AssertionError('юзер без токена не должен опрашиваться')

    main(fetch_friends=_fail, delay_seconds=0)

    db.refresh(user)
    assert user.vk_friends_data == [{'id': 'x'}]


def test_dry_run_writes_nothing(db: Session):
    user = _make_user(db, 'dry', vk_access_token='tok', vk_friends_data=[{'id': 'old'}])

    main(fetch_friends=lambda _token: [{'id': 'new'}], dry_run=True, delay_seconds=0)

    db.refresh(user)
    assert user.vk_friends_data == [{'id': 'old'}]


def test_script_main_execution(db: Session, mocker):
    # Нет юзеров с токеном → main() отработает вхолостую, покрывая ветку __main__.
    mocker.patch('sys.argv', ['backfill_vk_friends'])
    script_path = os.path.abspath(backfill_vk_friends.__file__)
    runpy.run_path(script_path, run_name='__main__')
