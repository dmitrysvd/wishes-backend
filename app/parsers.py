import json
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException
from loguru import logger

from app.schemas import ItemInfoResponseSchema


def _get_wb_basket(nm_id: int):
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


def try_parse_item_by_link(
    link: str, html: str | None
) -> Optional[ItemInfoResponseSchema]:
    logger.info('Парсинг превью {link}', link=link)
    if not html or 'yandex' in link:
        response = httpx.get(link, follow_redirects=True)
        if not response.is_success:
            return None
        html = response.text
    if 'market.yandex.ru' in link:
        match = re.search(r'window.\__apiary\.deferredMetaGenerator\((.*?.)\);', html)
        if not match:
            return None
        meta_data_str = match.group(1)
        meta_data = json.loads(meta_data_str)
        attrs = {}
        for item in meta_data:
            if item['tagName'] == 'meta' and item['attrs'].get(
                'property', ''
            ).startswith('og:'):
                key = item['attrs']['property']
                value = item['attrs']['content']
                attrs[key] = value
        return ItemInfoResponseSchema(
            title=attrs['og:title'],
            image_url=attrs['og:image'],
            description=attrs['og:description'],
        )
    elif 'wildberries.ru' in link:
        match = re.search(r'catalog\/(\d+)', link)
        if not match:
            return None
        item_id = int(match.group(1))
        vol = item_id // 100000
        part = item_id // 1000
        basket = _get_wb_basket(item_id)
        base_url = f'https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{item_id}'
        api_response = httpx.get(f'{base_url}/info/ru/card.json')
        api_response.raise_for_status()
        api_data = api_response.json()
        return ItemInfoResponseSchema(
            title=api_data['imt_name'],
            description=api_data['description'],
            image_url=f'{base_url}/images/big/1.webp',  # type: ignore
        )

    soup = BeautifulSoup(html, features='html.parser')
    try:
        title = soup.select('meta[property="og:title"]')[0]['content']
        description = soup.select('meta[property="og:description"]')[0]['content']
        image_url = soup.select('meta[property="og:image"]')[0]['content']
        assert isinstance(title, str)
        assert isinstance(description, str)
        assert isinstance(image_url, str)
    except IndexError:
        return None
    return ItemInfoResponseSchema(
        title=title,
        description=description,
        image_url=image_url,  # type: ignore
    )
