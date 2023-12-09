from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    IS_DEBUG: bool
    VK_SERVICE_KEY: str
    VK_PROTECTED_KEY: str
    VK_APP_ID: int
    FIREBASE_KEY_PATH: Path
    TEST_TOKEN: str

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()  # type: ignore
