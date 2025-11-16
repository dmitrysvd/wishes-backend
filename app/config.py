from pathlib import Path
from typing import Optional

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
    CPU_ALERT_TRESHOLD: float = 70.0
    RAM_ALERT_TRESHOLD: float = 70.0
    URL_ROOT_PATH: str = '/'
    SENTRY_DSN: Optional[str] = None
    ADMIN_PASSWORD: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()  # type: ignore
