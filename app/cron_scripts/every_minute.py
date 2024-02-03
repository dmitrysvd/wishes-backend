import asyncio

import psutil

from app.alerts import alert_warning
from app.config import settings


async def check_cpu_usage():
    cpu_percent = psutil.cpu_percent()
    if cpu_percent > 70.0:
        await alert_warning(f'CPU usage: {cpu_percent}%')
    memory_usage = psutil.virtual_memory()
    if memory_usage.percent > 70.0:
        await alert_warning(f'RAM usage: {memory_usage.percent}%')


async def main():
    await check_cpu_usage()


if __name__ == '__main__':
    asyncio.run(main(), debug=settings.IS_DEBUG)
