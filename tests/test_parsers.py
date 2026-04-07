import pytest

from app.parsers import (
    ItemInfoParseError,
    _get_wb_basket,
    is_absolute_url,
    try_parse_item_by_link,
)
from app.schemas import ItemInfoResponseSchema


def test_get_wb_basket():
    assert _get_wb_basket(0) == '01'
    assert _get_wb_basket(143 * 100000) == '01'
    assert _get_wb_basket(144 * 100000) == '02'
    assert _get_wb_basket(288 * 100000) == '03'
    assert _get_wb_basket(432 * 100000) == '04'
    assert _get_wb_basket(720 * 100000) == '05'
    assert _get_wb_basket(1008 * 100000) == '06'
    assert _get_wb_basket(1062 * 100000) == '07'
    assert _get_wb_basket(1116 * 100000) == '08'
    assert _get_wb_basket(1170 * 100000) == '09'
    assert _get_wb_basket(1314 * 100000) == '10'
    assert _get_wb_basket(1602 * 100000) == '11'
    assert _get_wb_basket(1656 * 100000) == '12'
    assert _get_wb_basket(1920 * 100000) == '13'
    assert _get_wb_basket(2046 * 100000) == '14'
    assert _get_wb_basket(2190 * 100000) == '15'


def test_is_absolute_url():
    assert is_absolute_url('https://example.com/image.png') is True
    assert is_absolute_url('http://example.com/image.png') is True
    assert is_absolute_url('/image.png') is False
    assert is_absolute_url('image.png') is False


@pytest.mark.anyio
async def test_try_parse_item_by_link_generic_og_tags():
    html = """
    <html>
        <head>
            <meta property="og:title" content="Test Title">
            <meta property="og:description" content="Test Description">
            <meta property="og:image" content="https://example.com/image.png">
        </head>
    </html>
    """
    result = await try_parse_item_by_link('https://example.com/item', html=html)
    assert isinstance(result, ItemInfoResponseSchema)
    assert str(result.title) == 'Test Title'
    assert str(result.description) == 'Test Description'
    assert str(result.image_url) == 'https://example.com/image.png'


@pytest.mark.anyio
async def test_try_parse_item_by_link_relative_image():
    html = """
    <html>
        <head>
            <meta property="og:title" content="Test Title">
            <meta property="og:description" content="Test Description">
            <meta property="og:image" content="/images/item.png">
        </head>
    </html>
    """
    result = await try_parse_item_by_link('https://example.com/item', html=html)
    assert str(result.image_url) == 'https://example.com/images/item.png'


@pytest.mark.anyio
async def test_try_parse_item_by_link_missing_tags():
    html = '<html><body>No meta tags here</body></html>'
    with pytest.raises(ItemInfoParseError, match='Не найден тег метаданных'):
        await try_parse_item_by_link('https://example.com/item', html=html)


@pytest.mark.anyio
async def test_try_parse_item_by_link_wildberries(mocker):
    # https://www.wildberries.ru/catalog/12345678/detail.aspx
    link = 'https://www.wildberries.ru/catalog/12345678/detail.aspx'
    item_id = 12345678
    vol = item_id // 100000
    part = item_id // 1000
    basket = '01'  # 123 // 100000 = 0 (approx)

    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'imt_name': 'WB Title',
        'description': 'WB Description',
    }
    mock_response.raise_for_status = mocker.Mock()

    # Mock httpx.AsyncClient.get
    mock_get = mocker.patch(
        'httpx.AsyncClient.get', mocker.AsyncMock(return_value=mock_response)
    )

    result = await try_parse_item_by_link(link)
    assert result.title == 'WB Title'
    assert result.description == 'WB Description'
    assert '1.webp' in str(result.image_url)

    api_url = f'https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{item_id}/info/ru/card.json'
    mock_get.assert_called_once_with(api_url)


@pytest.mark.anyio
async def test_try_parse_item_by_link_yandex_market():
    html = (
        '<script>window.__apiary.deferredMetaGenerator(['
        '{"tagName":"meta","attrs":{"property":"og:title","content":"Ya Title"}},'
        '{"tagName":"meta","attrs":{"property":"og:description",'
        '"content":"Ya Description"}},'
        '{"tagName":"meta","attrs":{"property":"og:image","content":"https://ya.com/img.png"}}'
        ']);</script>'
    )
    result = await try_parse_item_by_link(
        'https://market.yandex.ru/product/1', html=html
    )
    assert result.title == 'Ya Title'
    assert result.description == 'Ya Description'
    assert str(result.image_url) == 'https://ya.com/img.png'


@pytest.mark.anyio
async def test_try_parse_item_by_link_yandex_market_request_html(mocker):
    # Mocking _request_ya_market_html to avoid real requests
    html = """
    <script>window.__apiary.deferredMetaGenerator([
        {"tagName":"meta","attrs":{"property":"og:title","content":"Ya Title"}}
    ]);</script>
    """
    mocker.patch(
        'app.parsers._request_ya_market_html', mocker.AsyncMock(return_value=html)
    )

    with pytest.raises(ItemInfoParseError, match='Не найдена картинка'):
        await try_parse_item_by_link('https://market.yandex.ru/product/1')


@pytest.mark.anyio
async def test_try_parse_item_by_link_yandex_market_parse_error(mocker):
    html = 'invalid html'
    with pytest.raises(
        ItemInfoParseError, match='Не найдена переменная с данными в ответе'
    ):
        await try_parse_item_by_link('https://market.yandex.ru/product/1', html=html)


@pytest.mark.anyio
async def test_try_parse_item_by_link_yandex_market_json_error(mocker):
    html = 'window.__apiary.deferredMetaGenerator({invalid json});'
    with pytest.raises(ItemInfoParseError, match='Ошибка парсинга json'):
        await try_parse_item_by_link('https://market.yandex.ru/product/1', html=html)


@pytest.mark.anyio
async def test_try_parse_item_by_link_wildberries_no_id():
    with pytest.raises(Exception, match='В URL не найден паттерн catalog/'):
        await try_parse_item_by_link('https://wildberries.ru/not-a-product')


@pytest.mark.anyio
async def test_try_parse_item_by_link_generic_request_error(mocker):
    mock_response = mocker.Mock()
    mock_response.is_success = False
    mock_response.status_code = 404
    mocker.patch('httpx.AsyncClient.get', mocker.AsyncMock(return_value=mock_response))

    with pytest.raises(ItemInfoParseError, match='Ошибка статуса ответа: 404'):
        await try_parse_item_by_link('https://generic.com/item')


@pytest.mark.anyio
async def test_request_ya_market_html_cc_redirect(mocker):
    from app.parsers import _request_ya_market_html

    mock_response_1 = mocker.Mock()
    mock_response_1.history = [
        mocker.Mock(),
        mocker.Mock(),
        mocker.Mock(url='https://market.yandex.ru/real-link'),
    ]

    mock_response_2 = mocker.Mock()
    mock_response_2.status_code = 200
    mock_response_2.text = 'success'
    mock_response_2.headers = {}

    mock_get = mocker.patch(
        'httpx.AsyncClient.get',
        mocker.AsyncMock(side_effect=[mock_response_1, mock_response_2]),
    )

    result = await _request_ya_market_html('https://market.yandex.ru/cc/short')
    assert result == 'success'
    assert mock_get.call_count == 2
