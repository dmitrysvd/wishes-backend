from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SECRET_KEY: str
    IS_DEBUG: bool
    VK_SERVICE_KEY: str
    # VK ID-приложения: веб и мобилка — ВСЕГДА разные app (консоль VK ID заводит
    # отдельные под платформы). `code` обменивается только под тем app, что его
    # выпустил; платформу бэк выбирает по схеме `redirect_uri` (см. exchange_vk_code).
    VK_APP_ID: int  # мобильный (нативный SDK, redirect vk<app_id>://…)
    VK_WEB_APP_ID: int  # веб (One Tap @vkid/sdk, redirect https-origin)
    FIREBASE_KEY_PATH: Path
    TEST_TOKEN: str
    MEDIA_ROOT: Path
    LOGS_DIR: Path
    URL_ROOT_PATH: str = '/'
    # Hawk (hawk.so) — трекер ошибок. Интеграционный токен проекта.
    HAWK_TOKEN: str | None = None
    ADMIN_PASSWORD: str | None = None
    FRONTEND_URL: str = 'https://hotelki.pro'
    # Разрешённые CORS-origin. Задаётся в .env JSON-списком, напр.
    # CORS_ALLOW_ORIGINS=["https://hotelki.pro","https://app.hotelki.pro"]
    CORS_ALLOW_ORIGINS: list[str] = ['https://hotelki.pro']
    # DATABASE_URL: str = 'sqlite:///db.sqlite'
    DATABASE_URL: str
    TEST_DATABASE_URL: str = 'sqlite://'

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')


settings = Settings()  # type: ignore
