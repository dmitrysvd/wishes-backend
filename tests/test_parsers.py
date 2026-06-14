import httpx
import pytest

from app.parsers import (
    ItemInfoParseError,
    is_absolute_url,
    try_parse_item_by_link,
)
from app.schemas import ItemInfoResponseSchema


def _client(handler) -> httpx.AsyncClient:
    # Клиент на MockTransport: тестируем реальную логику httpx без моков.
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    )


def test_is_absolute_url():
    assert is_absolute_url('https://example.com/image.png') is True
    assert is_absolute_url('http://example.com/image.png') is True
    assert is_absolute_url('/image.png') is False
    assert is_absolute_url('image.png') is False


# --- Generic Open Graph -------------------------------------------------------


@pytest.mark.anyio
async def test_generic_og_tags():
    html = """
    <html><head>
        <meta property="og:title" content="Test Title">
        <meta property="og:description" content="Test Description">
        <meta property="og:image" content="https://example.com/image.png">
    </head></html>
    """
    result = await try_parse_item_by_link('https://example.com/item', html=html)
    assert isinstance(result, ItemInfoResponseSchema)
    assert result.title == 'Test Title'
    assert result.description == 'Test Description'
    assert str(result.image_url) == 'https://example.com/image.png'


@pytest.mark.anyio
async def test_generic_relative_image():
    html = """
    <html><head>
        <meta property="og:title" content="Test Title">
        <meta property="og:image" content="/images/item.png">
    </head></html>
    """
    result = await try_parse_item_by_link('https://example.com/item', html=html)
    assert str(result.image_url) == 'https://example.com/images/item.png'


@pytest.mark.anyio
async def test_generic_description_optional():
    # Страницы без og:description парсятся, описание становится пустым.
    html = """
    <html><head>
        <meta property="og:title" content="Only Title">
        <meta property="og:image" content="https://example.com/i.png">
    </head></html>
    """
    result = await try_parse_item_by_link('https://example.com/item', html=html)
    assert result.description == ''


@pytest.mark.anyio
async def test_generic_description_without_content():
    # og:description есть, но без атрибута content — описание остаётся пустым.
    html = """
    <html><head>
        <meta property="og:title" content="T">
        <meta property="og:image" content="https://example.com/i.png">
        <meta property="og:description">
    </head></html>
    """
    result = await try_parse_item_by_link('https://example.com/item', html=html)
    assert result.description == ''


@pytest.mark.anyio
async def test_generic_missing_tags():
    html = '<html><body>No meta tags here</body></html>'
    with pytest.raises(ItemInfoParseError, match='Не найден тег метаданных'):
        await try_parse_item_by_link('https://example.com/item', html=html)


@pytest.mark.anyio
async def test_generic_tag_without_content():
    # Тег og:image присутствует, но без content — это тоже считаем отсутствием данных.
    html = """
    <html><head>
        <meta property="og:title" content="T">
        <meta property="og:image">
    </head></html>
    """
    with pytest.raises(ItemInfoParseError, match='Не найден тег метаданных'):
        await try_parse_item_by_link('https://example.com/item', html=html)


@pytest.mark.anyio
async def test_generic_fetch_success():
    html = """
    <html><head>
        <meta property="og:title" content="Generic Title">
        <meta property="og:description" content="Generic Description">
        <meta property="og:image" content="https://generic.com/image.png">
    </head></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    async with _client(handler) as client:
        result = await try_parse_item_by_link('https://generic.com/item', client=client)
    assert result.title == 'Generic Title'
    assert str(result.image_url) == 'https://generic.com/image.png'


@pytest.mark.anyio
async def test_generic_fetch_status_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with _client(handler) as client:
        with pytest.raises(ItemInfoParseError, match='Ошибка статуса ответа: 404'):
            await try_parse_item_by_link('https://generic.com/item', client=client)


# --- Wildberries --------------------------------------------------------------


WB_LINK = 'https://www.wildberries.ru/catalog/12345678/detail.aspx'


def _wb_basket_number(request: httpx.Request) -> int:
    # basket-04.wbbasket.ru -> 4
    return int(request.url.host.removeprefix('basket-').split('.')[0])


def _wb_handler(ok_basket: int | None = None, max_existing: int = 40):
    # Существующие basket-ы отвечают 404 (кроме нужного — 200), несуществующие хосты
    # не резолвятся (как в реальности), что имитируется ConnectError.
    def handler(request: httpx.Request) -> httpx.Response:
        n = _wb_basket_number(request)
        if n > max_existing:
            raise httpx.ConnectError('nxdomain')
        if n == ok_basket:
            return httpx.Response(
                200, json={'imt_name': 'WB Title', 'description': 'WB Description'}
            )
        return httpx.Response(404)

    return handler


@pytest.mark.anyio
async def test_wildberries_success():
    # Целевой basket — 04, что заставляет перебор пропустить 01..03.
    async with _client(_wb_handler(ok_basket=4)) as client:
        result = await try_parse_item_by_link(WB_LINK, client=client)
    assert result.title == 'WB Title'
    assert result.description == 'WB Description'
    assert str(result.image_url) == (
        'https://basket-04.wbbasket.ru/vol123/part12345/12345678/images/big/1.webp'
    )


@pytest.mark.anyio
async def test_wildberries_found_in_later_batch():
    # basket-40 за пределами первой пачки (1..32) — перебор должен расшириться.
    async with _client(_wb_handler(ok_basket=40, max_existing=45)) as client:
        result = await try_parse_item_by_link(WB_LINK, client=client)
    assert 'basket-40' in str(result.image_url)


@pytest.mark.anyio
async def test_wildberries_skips_connection_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        n = _wb_basket_number(request)
        if n == 1:
            raise httpx.ConnectError('no route')
        if n == 5:
            return httpx.Response(200, json={'imt_name': 'WB', 'description': 'D'})
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await try_parse_item_by_link(WB_LINK, client=client)
    assert 'basket-05' in str(result.image_url)


@pytest.mark.anyio
async def test_wildberries_not_found():
    # Карточки нет ни на одном существующем basket-е: перебор останавливается,
    # когда упирается в несуществующие хосты.
    async with _client(_wb_handler(ok_basket=None, max_existing=40)) as client:
        with pytest.raises(
            ItemInfoParseError, match='Карточка товара Wildberries не найдена'
        ):
            await try_parse_item_by_link(WB_LINK, client=client)


@pytest.mark.anyio
async def test_wildberries_hard_limit():
    # Аномалия: любой хост отвечает 404 и «существует» — срабатывает предохранитель.
    async with _client(_wb_handler(ok_basket=None, max_existing=10**9)) as client:
        with pytest.raises(
            ItemInfoParseError, match='Карточка товара Wildberries не найдена'
        ):
            await try_parse_item_by_link(WB_LINK, client=client)


@pytest.mark.anyio
async def test_wildberries_no_id():
    with pytest.raises(ItemInfoParseError, match='В URL не найден паттерн catalog/'):
        await try_parse_item_by_link('https://wildberries.ru/not-a-product')


# --- Yandex Market ------------------------------------------------------------


def _ya_html(*items: str) -> str:
    payload = ','.join(items)
    return (
        '<script>window.__apiary.deferredMetaGenerator=function(){};</script>'
        f'<script>window.__apiary.deferredMetaGenerator([{payload}]);</script>'
    )


_YA_TITLE = '{"tagName":"meta","attrs":{"property":"og:title","content":"Ya Title"}}'
_YA_DESC = (
    '{"tagName":"meta","attrs":{"property":"og:description","content":"Ya Desc"}}'
)
_YA_IMAGE = (
    '{"tagName":"meta","attrs":{"property":"og:image",'
    '"content":"https://ya.com/img.png"}}'
)


@pytest.mark.anyio
async def test_yandex_market_success():
    # Помимо og-тегов в payload есть не-meta тег, meta без attrs и не-og meta —
    # все они должны игнорироваться.
    html = _ya_html(
        '{"tagName":"link","attrs":{"rel":"canonical"}}',
        '{"tagName":"meta"}',
        '{"tagName":"meta","attrs":{"name":"description","content":"x"}}',
        _YA_TITLE,
        _YA_DESC,
        _YA_IMAGE,
    )
    result = await try_parse_item_by_link(
        'https://market.yandex.ru/product/1', html=html
    )
    assert result.title == 'Ya Title'
    assert result.description == 'Ya Desc'
    assert str(result.image_url) == 'https://ya.com/img.png'


@pytest.mark.anyio
async def test_yandex_market_no_anchor():
    with pytest.raises(
        ItemInfoParseError, match='Не найдена переменная с данными в ответе'
    ):
        await try_parse_item_by_link(
            'https://market.yandex.ru/product/1', html='invalid html'
        )


@pytest.mark.anyio
async def test_yandex_market_json_error():
    html = 'window.__apiary.deferredMetaGenerator({invalid json});'
    with pytest.raises(ItemInfoParseError, match='Ошибка парсинга json'):
        await try_parse_item_by_link('https://market.yandex.ru/product/1', html=html)


@pytest.mark.anyio
async def test_yandex_market_missing_image():
    html = _ya_html(_YA_TITLE)
    with pytest.raises(ItemInfoParseError, match='Не найдена картинка'):
        await try_parse_item_by_link('https://market.yandex.ru/product/1', html=html)


@pytest.mark.anyio
async def test_yandex_market_missing_title():
    html = _ya_html(_YA_IMAGE)
    with pytest.raises(ItemInfoParseError, match='Не найден заголовок'):
        await try_parse_item_by_link('https://market.yandex.ru/product/1', html=html)


@pytest.mark.anyio
async def test_yandex_market_fetch_without_html():
    html = _ya_html(_YA_TITLE, _YA_IMAGE)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    async with _client(handler) as client:
        result = await try_parse_item_by_link(
            'https://market.yandex.ru/product/1', client=client
        )
    assert result.title == 'Ya Title'


@pytest.mark.anyio
async def test_yandex_market_cc_redirect():
    final_html = _ya_html(_YA_TITLE, _YA_IMAGE)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # Цепочка из трёх редиректов, как на коротких /cc/ ссылках.
        redirects = {
            '/cc/short': '/r1',
            '/r1': '/r2',
            '/r2': '/r3',
        }
        if path in redirects:
            return httpx.Response(
                302, headers={'location': f'https://market.yandex.ru{redirects[path]}'}
            )
        return httpx.Response(200, text=final_html)

    async with _client(handler) as client:
        result = await try_parse_item_by_link(
            'https://market.yandex.ru/cc/short', client=client
        )
    assert result.title == 'Ya Title'
