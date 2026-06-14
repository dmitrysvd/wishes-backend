"""Разовый сборщик рекомендаций с «Читай-город».

Запуск (нужен прямой RU-egress; в namespace Claude — через netns хоста):

    sudo nsenter -t 1 -n env -u http_proxy -u https_proxy -u HTTP_PROXY \
        -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
        uv run python scripts/collect_recommendations.py > scripts/recommendations.json

Логика: для каждой категории берём страницу каталога, вытаскиваем ссылки на
карточки товаров, затем по каждой карточке парсим название/цену/картинку из
og-метатегов и microdata. Источник отдаёт чистый HTML (не SPA) и не троттлит.

WB отвалился из-за анти-бота (товары придерживаются без браузерной сессии),
поэтому источник сменён на «Читай-город» — там данные лежат прямо в HTML.

target_gender уходит только в JSON (в схему БД пока не пишем); 'neutral' =
показывать обоим полам, 'male'/'female' = смещение в будущей выдаче.
"""

import html
import json
import re
import sys
import time

import httpx

BASE = 'https://www.chitai-gorod.ru'

# (путь категории, target_gender). Большинство подарочных категорий нейтральны;
# пара книжных жанров даёт слабый гендерный сигнал для будущей сортировки.
# Гендерные категории идут первыми, чтобы их не срезал лимит TARGET_TOTAL.
CATEGORIES: list[tuple[str, str]] = [
    ('/catalog/books/biznes-predprinimatelstvo-torgovlya-110372', 'male'),
    ('/catalog/books/dom-i-hobbi-110244', 'female'),
    ('/catalog/souvenirs-18038', 'neutral'),
    ('/catalog/hobbies-18200', 'neutral'),
    ('/catalog/toys-18039', 'neutral'),
    ('/catalog/artists-110621', 'neutral'),
    ('/catalog/eda-i-napitki-115662', 'neutral'),
    ('/catalog/books/psihologiya-110321', 'neutral'),
]

# Сколько товаров берём с каждой категории и сколько всего.
PER_CATEGORY = 7
TARGET_TOTAL = 50

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
    ),
    'Accept-Language': 'ru-RU,ru;q=0.9',
}

PRODUCT_RE = re.compile(r'/product/[a-z0-9-]+-(\d+)')
PRICE_RE = re.compile(r'itemprop="price"[^>]*content="([0-9.]+)"')
OG_RE = {
    'image': re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"'),
    'title': re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"'),
    'description': re.compile(
        r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"'
    ),
}


def fetch(url: str, client: httpx.Client) -> str | None:
    for attempt in range(4):
        resp = client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 2000:
            return resp.text
        wait = 2**attempt
        print(f'[{url}] HTTP {resp.status_code}, retry in {wait}s', file=sys.stderr)
        time.sleep(wait)
    print(f'[{url}] gave up', file=sys.stderr)
    return None


def og(page: str, key: str) -> str | None:
    m = OG_RE[key].search(page)
    return html.unescape(m.group(1)).strip() if m else None


def clean_title(raw: str | None) -> str | None:
    if not raw:
        return None
    # og:title вида «Название 🎁 купить ...» — отрезаем маркетинговый хвост
    # и подчищаем оставшиеся эмодзи в конце.
    title = re.split(r'\s+купить\b', raw)[0]
    title = re.sub(r'[\U0001F000-\U0001FAFF☀-➿️\s]+$', '', title)
    return title.strip()[:250] or None


def parse_product(url: str, page: str, target_gender: str) -> dict | None:
    title = clean_title(og(page, 'title'))
    if not title:
        return None
    price = None
    m = PRICE_RE.search(page)
    if m:
        price = round(float(m.group(1)), 2)
    description = og(page, 'description')
    if description:
        description = description[:1000]
    return {
        'title': title,
        'description': description,
        'price': price,
        'link': url,
        'image_url': og(page, 'image'),
        'target_gender': target_gender,
    }


def product_links(catalog_page: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for m in PRODUCT_RE.finditer(catalog_page):
        path = m.group(0)
        if path not in seen:
            seen.add(path)
            links.append(BASE + path)
    return links


def main() -> None:
    seen_ids: set[str] = set()
    out: list[dict] = []
    with httpx.Client() as client:
        for path, target_gender in CATEGORIES:
            catalog = fetch(BASE + path, client)
            if not catalog:
                continue
            taken = 0
            for url in product_links(catalog):
                pid = PRODUCT_RE.search(url).group(1)  # ty: ignore[unresolved-attribute]
                if pid in seen_ids:
                    continue
                page = fetch(url, client)
                if not page:
                    continue
                rec = parse_product(url, page, target_gender)
                time.sleep(0.5)
                if not rec:
                    continue
                seen_ids.add(pid)
                out.append(rec)
                taken += 1
                if taken >= PER_CATEGORY:
                    break
            print(f'[{path}] took {taken}', file=sys.stderr)
            if len(out) >= TARGET_TOTAL:
                break

    json.dump(out[:TARGET_TOTAL], sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')
    print(f'collected {min(len(out), TARGET_TOTAL)} items', file=sys.stderr)


if __name__ == '__main__':
    main()
