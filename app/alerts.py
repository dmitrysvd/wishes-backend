import sys
import traceback

import httpx
from fastapi.requests import Request

from app.config import settings


async def send_alert_message(msg: str):
    api_url = f'https://api.telegram.org/bot{settings.TG_ALERTS_BOT_TOKEN}/sendMessage'
    async with httpx.AsyncClient() as client:
        tg_response = await client.post(
            api_url,
            json={'chat_id': settings.TG_ALERTS_CHANNEL_ID, 'text': msg},
        )
        tg_response.raise_for_status()


async def alert_exception(request: Request, exception: Exception):
    _, _, tb = sys.exc_info()
    assert tb is not None
    tb_text = '\n'.join(traceback.format_tb(tb, limit=-3))
    message = f'{request.url}\n\n{repr(exception)}\n\n{str(exception)}\n\n{tb_text}'
    await send_alert_message(message)


async def alert_warning(message: str):
    await send_alert_message(f'Warning:\n\n{message}')
