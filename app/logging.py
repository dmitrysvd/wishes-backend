from loguru import logger

from app.config import settings

logger.add(settings.LOGS_DIR / 'log.log')
