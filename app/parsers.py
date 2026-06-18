import asyncio
import ipaddress
import json
import re
import socket
import urllib.parse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.schemas import ItemInfoResponseSchema

# Таймаут по умолчанию для исходящих запросов парсера.
DEFAULT_TIMEOUT = 10

# Парсер ходит по ссылке, которую прислал пользователь, поэтому защищаемся от SSRF:
# разрешаем только http(s) и запрещаем обращения во внутреннюю сеть (loopback,
# приватные/link-local диапазоны, метаданные облака 169.254.169.254 и т.п.).
ALLOWED_URL_SCHEMES = ('http', 'https')

# Wildberries хранит карточки на basket-хостах (basket-01..basket-NN.wbbasket.ru).
# Номер basket-а раньше вычислялся статической таблицей из их JS, но WB регулярно
# добавляет новые хосты, поэтому таблица быстро устаревает. Вместо неё перебираем
# basket-ы параллельно пачками и берём тот, что реально отдал карточку.
#
# Чтобы не хардкодить «потолок» по числу хостов, перебор расширяется сам: существующий,
# но «чужой» basket отвечает 404, а несуществующий хост не резолвится (ошибка
# соединения). Если в очередной пачке даже самый старший хост не существует — значит
# выше basket-ов нет и искать дальше бессмысленно. Новые basket-ы подхватятся сами.
WB_BASKET_BATCH = 32
# Предохранитель от бесконечного цикла, если WB вдруг начнёт отвечать на любой хост.
WB_BASKET_HARD_LIMIT = 257

# Заголовки браузера для обхода защиты Яндекс.Маркета на коротких ссылках.
YA_MARKET_HEADERS = {
    'authority': 'market.yandex.ru',
    'accept': (
        'text/html,application/xhtml+xml,application/xml;q=0.9,'
        'image/avif,image/webp,image/apng,*/*;q=0.8,'
        'application/signed-exchange;v=b3;q=0.7'
    ),
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
    'user-agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    ),
}

# Якорь, после которого в html Яндекс.Маркета идёт JSON с meta-тегами страницы.
YA_MARKET_META_ANCHOR = 'window.__apiary.deferredMetaGenerator('


class ItemInfoParseError(Exception):
    pass


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    # Отсекаем всё, что ведёт во внутреннюю инфраструктуру или к спец-адресам.
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _assert_public_url(url: str) -> None:
    """Проверить, что URL безопасен для исходящего запроса (защита от SSRF)."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise ItemInfoParseError(f'Недопустимая схема URL: {parsed.scheme}')
    host = parsed.hostname
    if not host:
        raise ItemInfoParseError('В URL не найден хост')
    try:
        addr_infos = socket.getaddrinfo(host, parsed.port or None)
    except socket.gaierror as exc:
        raise ItemInfoParseError(f'Не удалось разрешить хост: {host}') from exc
    # Все IP, в которые резолвится хост, должны быть публичными — иначе это попытка
    # достучаться до внутренней сети (в т.ч. через DNS, указывающий на 127.0.0.1).
    for addr_info in addr_infos:
        ip = str(addr_info[4][0])
        if not _is_public_ip(ip):
            raise ItemInfoParseError(f'Обращение к внутреннему адресу запрещено: {ip}')


async def _block_internal_requests(request: httpx.Request) -> None:
    # Хук вызывается httpx на каждый запрос, включая каждый redirect-хоп, поэтому
    # внутренний адрес нельзя протащить ни через исходную ссылку, ни через редирект.
    _assert_public_url(str(request.url))


def is_absolute_url(url: str) -> bool:
    parsed_url = urllib.parse.urlparse(url)
    return bool(parsed_url.netloc)


def _extract_og_attrs(meta_items: list[dict]) -> dict[str, str]:
    # Собираем og:-теги из списка дескрипторов meta-тегов Яндекс.Маркета.
    attrs: dict[str, str] = {}
    for item in meta_items:
        if item.get('tagName') != 'meta':
            continue
        item_attrs = item.get('attrs', {})
        prop = item_attrs.get('property', '')
        if prop.startswith('og:'):
            attrs[prop] = item_attrs.get('content', '')
    return attrs


async def _parse_ya_market_page(html: str) -> ItemInfoResponseSchema:
    idx = html.find(YA_MARKET_META_ANCHOR)
    if idx == -1:
        logger.debug(
            'html content:\n{html}\n\n{html_repr}', html=html, html_repr=repr(html)
        )
        raise ItemInfoParseError('Не найдена переменная с данными в ответе')
    # raw_decode разбирает ровно один JSON-объект и игнорирует хвост скрипта,
    # поэтому устойчив к скобкам и кавычкам внутри значений (в отличие от регулярки).
    try:
        meta_data, _ = json.JSONDecoder().raw_decode(
            html, idx + len(YA_MARKET_META_ANCHOR)
        )
    except json.JSONDecodeError as exc:
        raise ItemInfoParseError('Ошибка парсинга json') from exc
    attrs = _extract_og_attrs(meta_data)
    if 'og:title' not in attrs:
        raise ItemInfoParseError('Не найден заголовок')
    # На анти-бот/деградированной странице og:image — протокол-относительная
    # заглушка (`//yastatic.net/.../big-box.png`): netloc есть, а scheme нет, поэтому
    # is_absolute_url её пропускает, но HttpUrl падает. Требуем абсолютный http(s) URL,
    # иначе это не настоящая карточка — отдаём понятную ошибку вместо 500.
    image_parsed = urllib.parse.urlparse(attrs.get('og:image', ''))
    if image_parsed.scheme not in ('http', 'https') or not image_parsed.netloc:
        raise ItemInfoParseError('Не найдена картинка')
    return ItemInfoResponseSchema(
        title=attrs['og:title'],
        image_url=attrs['og:image'],  # type: ignore
        description=attrs.get('og:description', ''),
    )


async def _request_ya_market_html(link: str, client: httpx.AsyncClient) -> str:
    if '/cc/' in link:
        # Короткие ссылки вызывают несколько редиректов, заканчивающихся каптчей (400).
        # Запрос итоговой страницы повторно возвращает успешный ответ.
        response = await client.get(link, headers=YA_MARKET_HEADERS)
        link = str(response.history[2].url)
    # Браузерные заголовки обязательны: без них Яндекс отдаёт серверу деградированную
    # анти-бот страницу с картинкой-заглушкой вместо реальной карточки.
    response = await client.get(link, headers=YA_MARKET_HEADERS)
    logger.debug(
        'ya market link {link}, status {status}, headers {headers}',
        link=link,
        status=response.status_code,
        headers=str(response.headers),
    )
    return response.text


async def _parse_wildberries(
    item_id: int, client: httpx.AsyncClient
) -> ItemInfoResponseSchema:
    vol = item_id // 100000
    part = item_id // 1000
    start = 1
    while start < WB_BASKET_HARD_LIMIT:
        base_urls = [
            f'https://basket-{n:02d}.wbbasket.ru/vol{vol}/part{part}/{item_id}'
            for n in range(start, start + WB_BASKET_BATCH)
        ]
        # Перебираем пачку basket-хостов параллельно: карточку отдаёт ровно один из них.
        responses = await asyncio.gather(
            *(client.get(f'{base_url}/info/ru/card.json') for base_url in base_urls),
            return_exceptions=True,
        )
        for base_url, response in zip(base_urls, responses, strict=True):
            if isinstance(response, BaseException) or not response.is_success:
                continue
            api_data = response.json()
            return ItemInfoResponseSchema(
                title=api_data['imt_name'],
                description=api_data.get('description', ''),
                image_url=f'{base_url}/images/big/1.webp',  # type: ignore
            )
        # Самый старший хост пачки не существует → basket-ов выше нет, дальше не ищем.
        if isinstance(responses[-1], BaseException):
            break
        start += WB_BASKET_BATCH
    raise ItemInfoParseError('Карточка товара Wildberries не найдена')


def _parse_og_tags(link: str, html: str) -> ItemInfoResponseSchema:
    soup = BeautifulSoup(html, features='html.parser')
    title_tag = soup.select_one('meta[property="og:title"]')
    image_tag = soup.select_one('meta[property="og:image"]')
    if title_tag is None or image_tag is None:
        raise ItemInfoParseError('Не найден тег метаданных')
    title = title_tag.get('content')
    image_url = image_tag.get('content')
    if not isinstance(title, str) or not isinstance(image_url, str):
        raise ItemInfoParseError('Не найден тег метаданных')
    # og:description есть не на всех страницах — он необязателен.
    description_tag = soup.select_one('meta[property="og:description"]')
    description = description_tag.get('content') if description_tag else None
    if not isinstance(description, str):
        description = ''
    if not is_absolute_url(image_url):
        # если был указан относительный путь, конструируем абсолютный путь,
        # используя link, чтобы фронт смог подтянуть картинку.
        base_url_parsed = urllib.parse.urlparse(link)
        image_url = urllib.parse.urlunparse(
            (
                base_url_parsed.scheme,
                base_url_parsed.netloc,
                image_url,
                '',
                '',
                '',
            )
        )
    return ItemInfoResponseSchema(
        title=title,
        description=description,
        image_url=image_url,  # type: ignore
    )


async def _fetch_html(link: str, client: httpx.AsyncClient) -> str:
    response = await client.get(link)
    if not response.is_success:
        raise ItemInfoParseError(f'Ошибка статуса ответа: {response.status_code}')
    return response.text


async def try_parse_item_by_link(
    link: str,
    html: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> ItemInfoResponseSchema:
    logger.info(
        'Парсинг превью {link}, есть html: {has_html}', link=link, has_html=bool(html)
    )
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT,
            event_hooks={'request': [_block_internal_requests]},
        )
    try:
        if 'market.yandex.ru' in link:
            if not html:
                html = await _request_ya_market_html(link, client)
            return await _parse_ya_market_page(html)

        if 'wildberries.ru' in link:
            match = re.search(r'catalog/(\d+)', link)
            if not match:
                raise ItemInfoParseError('В URL не найден паттерн catalog/')
            return await _parse_wildberries(int(match.group(1)), client)

        if not html:
            html = await _fetch_html(link, client)
        return _parse_og_tags(link, html)
    finally:
        if own_client:
            await client.aclose()
