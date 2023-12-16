import json
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException
from loguru import logger

from app.schemas import ItemInfoSchema


def try_parse_item_by_link(link: str) -> Optional[ItemInfoSchema]:
    logger.info('Парсинг превью {link}', link=link)
    response = httpx.get(link, follow_redirects=True)
    response.raise_for_status()
    content = response.content
    if 'market.yandex.ru' in link:
        match = re.search(
            r'window.\__apiary\.deferredMetaGenerator\((.*?.)\);', response.text
        )
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
        return ItemInfoSchema(
            title=attrs['og:title'],
            image_url=attrs['og:image'],
            description=attrs['og:description'],
        )
    elif 'wildberries.ru' in link:
        match = re.search(r'catalog\/(\d+)', link)
        if not match:
            return None
        item_id = match.group(1)
        vol = item_id[:3]
        part = item_id[:5]
        base_url = f'https://basket-02.wbbasket.ru/vol{vol}/part{part}/{item_id}'
        api_response = httpx.get(f'{base_url}/info/ru/card.json')
        api_response.raise_for_status()
        api_data = api_response.json()
        return ItemInfoSchema(
            title=api_data['imt_name'],
            description=api_data['description'],
            image_url=f'{base_url}/images/big/1.webp',  # type: ignore
        )

    soup = BeautifulSoup(content, features='html.parser')
    try:
        title = soup.select('meta[property="og:title"]')[0]['content']
        description = soup.select('meta[property="og:description"]')[0]['content']
        image_url = soup.select('meta[property="og:image"]')[0]['content']
        assert isinstance(title, str)
        assert isinstance(description, str)
        assert isinstance(image_url, str)
    except IndexError:
        return None
    return ItemInfoSchema(
        title=title,
        description=description,
        image_url=image_url,  # type: ignore
    )
