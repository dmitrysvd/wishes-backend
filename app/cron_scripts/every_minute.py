import asyncio

import psutil

from app.alerts import alert_warning
from app.config import settings


async def check_cpu_usage():
    cpu_percent = psutil.cpu_percent()
    if cpu_percent > settings.CPU_ALERT_TRESHOLD:
        alert_warning(f'CPU usage: {cpu_percent}%')
    memory_usage = psutil.virtual_memory()
    if memory_usage.percent > settings.RAM_ALERT_TRESHOLD:
        alert_warning(f'RAM usage: {memory_usage.percent}%')


def main():
    pass


if __name__ == '__main__':
    main()
