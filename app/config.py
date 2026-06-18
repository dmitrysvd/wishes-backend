from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SECRET_KEY: str
    IS_DEBUG: bool
    VK_SERVICE_KEY: str
    VK_APP_ID: int
    FIREBASE_KEY_PATH: Path
    TEST_TOKEN: str
    MEDIA_ROOT: Path
    LOGS_DIR: Path
    TG_ALERTS_BOT_TOKEN: str
    TG_ALERTS_CHANNEL_ID: str
    URL_ROOT_PATH: str = '/'
    SENTRY_DSN: str | None = None
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
