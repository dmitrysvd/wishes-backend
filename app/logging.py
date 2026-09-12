from loguru import logger

from app.config import settings
from app.hawk import hawk, make_hawk_sink

log_level = 'DEBUG' if settings.IS_DEBUG else 'INFO'
logger.add(
    settings.LOGS_DIR / 'log.log',
    level=log_level,
    backtrace=False,
    diagnose=False,
)
logger.add(
    settings.LOGS_DIR / 'errors.log',
    level='ERROR',
    backtrace=True,
    diagnose=True,
)
# Всё уровня ERROR — в Hawk (из любого процесса: app, scheduler).
logger.add(make_hawk_sink(hawk), level='ERROR')
