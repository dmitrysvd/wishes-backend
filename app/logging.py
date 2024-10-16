from loguru import logger

from app.config import settings

log_level = 'DEBUG' if settings.IS_DEBUG else 'INFO'
logger.add(settings.LOGS_DIR / 'log.log', level=log_level)
