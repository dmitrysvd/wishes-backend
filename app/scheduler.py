import logging
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.cron_scripts import at_noon, every_hour, every_minute
from app.logging import logger

# Set APScheduler logging to WARNING to keep the output clean
logging.getLogger('apscheduler').setLevel(logging.WARNING)

# Heartbeat-файл: обновляется при каждом запуске задачи (every_minute — раз в
# минуту). По его свежести docker-healthcheck понимает, что планировщик реально
# тикает, а не просто что процесс жив.
HEARTBEAT_FILE = Path('/tmp/scheduler_heartbeat')


def run_job(job_func, job_name):
    logger.info(f'Running job: {job_name}')
    HEARTBEAT_FILE.touch()
    try:
        job_func()
    except Exception:
        logger.exception(f'Error running job {job_name}')


def start_scheduler():
    scheduler = BlockingScheduler()

    # Schedule: every minute
    scheduler.add_job(
        run_job,
        CronTrigger(minute='*'),
        args=[every_minute.main, 'every_minute'],
        id='every_minute_job',
    )

    # Schedule: every hour (at :00)
    scheduler.add_job(
        run_job,
        CronTrigger(minute=0),
        args=[every_hour.main, 'every_hour'],
        id='every_hour_job',
    )

    # Schedule: every day at noon (12:00)
    scheduler.add_job(
        run_job,
        CronTrigger(hour=12, minute=0),
        args=[at_noon.main, 'at_noon'],
        id='at_noon_job',
    )

    logger.info('Scheduler started')
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info('Scheduler stopped')


if __name__ == '__main__':
    start_scheduler()
