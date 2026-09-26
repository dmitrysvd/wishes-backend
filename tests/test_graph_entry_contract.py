"""Тесты формы контракта фичи 0024 (вход в граф без усилий)."""

from app.main import app


def test_auth_firebase_is_public_and_returns_json():
    operation = app.openapi()['paths']['/auth/firebase']['post']
    assert operation['security'] == []
    schema = operation['responses']['200']['content']['application/json']['schema']
    assert schema == {'$ref': '#/components/schemas/AuthFirebaseResponseSchema'}
    assert '403' in operation['responses']


def test_auth_responses_carry_mutual_follow_user_id():
    schemas = app.openapi()['components']['schemas']
    for name in ('AuthFirebaseResponseSchema', 'ResponseVkAuthMobileSchema'):
        # required + nullable: поле всегда в ответе, `null` — ребра нет.
        assert 'mutual_follow_user_id' in schemas[name]['required']


def test_follow_source_enum_has_follow_back_values():
    enum = app.openapi()['components']['schemas']['FollowSource']['enum']
    assert {'push', 'followers_follow_back', 'deeplink'} <= set(enum)


def test_push_links_to_profiles_carry_push_marker():
    """Профиль из пуша отличим от ссылки шеринга только по `via=push`."""
    paths = app.openapi()['paths']
    payloads = [
        paths['/follow/{follow_user_id}']['post']['x-push-payload'],
        paths['/auth/firebase']['post']['x-push-payload'],
        paths['/auth/vk/vkid']['post']['x-push-payload'],
    ]
    for payload in payloads:
        assert '&via=push' in payload['data']['link']
        assert 'delivery_id' in payload['data']
