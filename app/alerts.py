import sys
import traceback

import httpx
from fastapi.requests import Request

from app.config import settings


async def alert_tg(request: Request, exception: Exception):
    api_url = f'https://api.telegram.org/bot{settings.TG_ALERTS_BOT_TOKEN}/sendMessage'
    _, _, tb = sys.exc_info()
    assert tb is not None

    tb_text = '\n'.join(traceback.format_tb(tb, limit=-3))
    message = f'{request.url}\n\n{tb_text}'

    async with httpx.AsyncClient() as client:
        tg_response = await client.post(
            api_url,
            json={'chat_id': settings.TG_ALERTS_CHANNEL_ID, 'text': message},
        )
        tg_response.raise_for_status()
