import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.constants import Gender
from app.db import Base, User
from app.main import app, get_current_user, get_db

engine = create_engine(
    'sqlite://',
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


Base.metadata.create_all(bind=engine)

api_client = TestClient(app)


@pytest.fixture
def db():
    try:
        _db = TestingSessionLocal()
        yield _db
    finally:
        _db.close()


@pytest.fixture
def user(db: Session):
    _user = User(
        display_name='Test user',
        email='test@mail.ru',
        photo_url='test_photo.com',
        vk_id='vk_id',
        vk_friends_data=[],
        vk_access_token='vk_access_token',
        firebase_uid='firebase uid',
        gender=Gender.male,
    )
    db.add(_user)
    db.commit()
    return _user


@pytest.fixture(autouse=True)
def override_dependencies(user, db):
    def override_get_db():
        return db

    def override_get_current_user():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user


@pytest.fixture
def auth_client(user: User) -> TestClient:
    client = TestClient(app, headers={'Authorization': ''})
    return client


class TestGetWishes:
    def test_empty_wishes(self, auth_client):
        response = auth_client.get('/wishes')
        assert response.is_success
        assert response.json() == []


class TestUpdateOwnUser:
    def test_update_name(self, user: User, auth_client: TestClient, db: Session):
        response = auth_client.put(
            '/users/me',
            json={
                'display_name': 'New name',
                'gender': 'male',
                'birth_date': '2000-01-01',
            },
        )
        assert response.is_success, response.json()
        user = db.scalars(select(User).where(User.id == user.id)).one()
        assert user.display_name == 'New name'
