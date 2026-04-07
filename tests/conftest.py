import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base


@pytest.fixture(scope='session', autouse=True)
def test_engine():
    engine = create_engine(settings.TEST_DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db(test_engine, mocker):
    connection = test_engine.connect()
    transaction = connection.begin()

    # Create session bound to the connection
    session_factory = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    session = session_factory()

    # Wrap session to ignore close() calls from the app
    class SessionWrapper:
        def __init__(self, session):
            self._session = session

        def __enter__(self):
            return self._session

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def close(self):
            pass

        def __getattr__(self, name):
            return getattr(self._session, name)

    wrapper = SessionWrapper(session)

    # Patch SessionLocal everywhere it is used
    modules = [
        'app.db',
        'app.dependencies',
        'app.notifications',
        'app.cron_scripts.at_noon',
    ]
    for module in modules:
        try:
            mocker.patch(f'{module}.SessionLocal', return_value=wrapper)
        except (ImportError, AttributeError):
            pass

    nested = connection.begin_nested()
    from sqlalchemy import event

    @event.listens_for(session, 'after_transaction_end')
    def _restart_savepoint_after_commit(sess, trans):
        nonlocal nested
        if trans.nested and not trans._parent.nested:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
