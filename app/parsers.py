import json
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException
from loguru import logger

from app.schemas import ItemInfoResponseSchema


class ItemInfoParseError(Exception):
    pass


def _get_wb_basket(nm_id: int):
    # Взято из исходников js-файла на сайте.
    nm_id = nm_id // 100000
    if nm_id >= 0 and nm_id <= 143:
        return "01"
    if nm_id >= 144 and nm_id <= 287:
        return "02"
    if nm_id >= 288 and nm_id <= 431:
        return "03"
    if nm_id >= 432 and nm_id <= 719:
        return "04"
    if nm_id >= 720 and nm_id <= 1007:
        return "05"
    if nm_id >= 1008 and nm_id <= 1061:
        return "06"
    if nm_id >= 1062 and nm_id <= 1115:
        return "07"
    if nm_id >= 1116 and nm_id <= 1169:
        return "08"
    if nm_id >= 1170 and nm_id <= 1313:
        return "09"
    if nm_id >= 1314 and nm_id <= 1601:
        return "10"
    if nm_id >= 1602 and nm_id <= 1655:
        return "11"
    if nm_id >= 1656 and nm_id <= 1919:
        return "12"
    if nm_id >= 1920 and nm_id <= 2045:
        return "13"
    if nm_id >= 2046 and nm_id <= 2189:
        return "14"
    return "15"


async def _parse_ya_market_page(html: str) -> ItemInfoResponseSchema:
    match = re.search(r'window.\__apiary\.deferredMetaGenerator\((.*?.)\);', html)
    if not match:
        raise ItemInfoParseError('Не найдена переменная с данными в ответе')
    meta_data_str = match.group(1)
    meta_data = json.loads(meta_data_str)
    attrs = {}
    for item in meta_data:
        if item['tagName'] == 'meta' and item['attrs'].get('property', '').startswith(
            'og:'
        ):
            key = item['attrs']['property']
            value = item['attrs']['content']
            attrs[key] = value
    return ItemInfoResponseSchema(
        title=attrs['og:title'],
        image_url=attrs['og:image'],
        description=attrs.get('og:description', ''),
    )


async def _request_ya_market_html(link: str) -> str:
    if '/cc/' in link:
        # Короткие ссылки вызывают несколько редиректов, заканчивающихся каптчей (400).
        # Запрос той же страницы повторно возвращает успешный ответ.
        async with httpx.AsyncClient() as client:
            client.headers = {
                'authority': 'market.yandex.ru',
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'accept-language': 'en-US,en;q=0.9',
                'cache-control': 'no-cache',
                'pragma': 'no-cache',
                'sec-ch-ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Linux"',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'none',
                'sec-fetch-user': '?1',
                'upgrade-insecure-requests': '1',
                'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            }
            response = await client.get(
                link,
                follow_redirects=True,
            )
            next_url = response.history[2].url
            link = str(next_url)
    async with httpx.AsyncClient() as client:
        response_2 = await client.get(link)
        return response_2.text


async def try_parse_item_by_link(
    link: str,
    html: str | None = None,
) -> ItemInfoResponseSchema:
    logger.info(
        'Парсинг превью {link}, есть html: {has_html}', link=link, has_html=bool(html)
    )

    if 'market.yandex.ru' in link:
        html = await _request_ya_market_html(link)
        return await _parse_ya_market_page(html)

    if 'wildberries.ru' in link:
        match = re.search(r'catalog\/(\d+)', link)
        if not match:
            raise Exception('В URL не найден паттерн catalog/')
        item_id = int(match.group(1))
        vol = item_id // 100000
        part = item_id // 1000
        basket = _get_wb_basket(item_id)
        base_url = f'https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{item_id}'
        async with httpx.AsyncClient() as client:
            api_response = await client.get(f'{base_url}/info/ru/card.json')
        api_response.raise_for_status()
        api_data = api_response.json()
        return ItemInfoResponseSchema(
            title=api_data['imt_name'],
            description=api_data['description'],
            image_url=f'{base_url}/images/big/1.webp',  # type: ignore
        )

    if not html:
        async with httpx.AsyncClient() as client:
            response = await client.get(link, follow_redirects=True, timeout=5)
        if not response.is_success:
            raise ItemInfoParseError(f'Ошибка статуса ответа: {response.status_code}')
        html = response.text

    soup = BeautifulSoup(html, features='html.parser')
    try:
        title = soup.select('meta[property="og:title"]')[0]['content']
        description = soup.select('meta[property="og:description"]')[0]['content']
        image_url = soup.select('meta[property="og:image"]')[0]['content']
        assert isinstance(title, str)
        assert isinstance(description, str)
        assert isinstance(image_url, str)
    except IndexError:
        raise ItemInfoParseError('Не найден тег метаданных')
    return ItemInfoResponseSchema(
        title=title,
        description=description,
        image_url=image_url,  # type: ignore
    )
