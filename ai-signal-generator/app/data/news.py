"""
Crypto news fetcher using CoinGecko API and RSS feeds (no API key required).
Sources: CoinGecko news, CoinDesk RSS, Cointelegraph RSS.
All functions are async. Failures are non-fatal — return None on error.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import feedparser
import httpx

logger = logging.getLogger(__name__)

_RSS_FEEDS = {
    'coindesk':       'https://www.coindesk.com/arc/outboundfeeds/rss/',
    'cointelegraph':  'https://cointelegraph.com/rss',
}

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='rss')


def _parse_rss_sync(name: str, url: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:15]:
            headline = entry.get('title', '').strip()
            if headline:
                items.append({
                    'headline': headline,
                    'url':      entry.get('link', ''),
                    'source':   name,
                    'severity': 'medium',
                })
        return items
    except Exception as exc:
        logger.warning("RSS parse error [%s]: %s", url, exc)
        return []


async def _fetch_rss(name: str, url: str) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _parse_rss_sync, name, url)


async def _fetch_coingecko_news() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.coingecko.com/api/v3/news")
            if resp.status_code != 200:
                return []
            payload = resp.json()
            articles = payload if isinstance(payload, list) else payload.get('data', [])
            items = []
            for a in articles[:15]:
                headline = (a.get('title') or '').strip()
                if not headline:
                    continue
                author = a.get('author', {})
                source = author.get('full_name', 'CoinGecko') if isinstance(author, dict) else 'CoinGecko'
                items.append({
                    'headline': headline,
                    'url':      a.get('url', ''),
                    'source':   source,
                    'severity': 'medium',
                })
            return items
    except Exception as exc:
        logger.warning("CoinGecko news error: %s", exc)
        return []


async def fetch_news(symbol: str = 'BTC') -> list[dict] | None:
    """
    Fetch recent crypto news from CoinGecko and RSS feeds.

    Returns list of dicts with keys: headline, url, source, severity.
    Returns None if all sources fail.
    """
    try:
        tasks = [asyncio.create_task(_fetch_coingecko_news())]
        for name, url in _RSS_FEEDS.items():
            tasks.append(asyncio.create_task(_fetch_rss(name, url)))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items: list[dict] = []
        for r in results:
            if isinstance(r, list):
                all_items.extend(r)

        if not all_items:
            logger.warning("fetch_news: all sources returned empty")
            return None

        seen: set[str] = set()
        deduped: list[dict] = []
        for item in all_items:
            key = item['headline'].lower()
            if key not in seen:
                seen.add(key)
                deduped.append(item)

        return deduped[:30]

    except Exception as exc:
        logger.warning("fetch_news error: %s", exc)
        return None


async def fetch_news_digest(lookback_hours: int = 24) -> dict | None:
    """
    Fetch news and return as a digest dict for use by the prompt builder.
    Returns: {'items': list[dict], 'lookback_hours': int} or None if all sources fail.
    """
    items = await fetch_news()
    if items is None:
        return None
    return {'items': items, 'lookback_hours': lookback_hours}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _test():
        news = await fetch_news('BTC')
        if news:
            print(f"Fetched {len(news)} news items:")
            for item in news[:8]:
                print(f"  [{item['source']:15s}] {item['headline'][:80]}")
        else:
            print("FAIL: No news returned")

    asyncio.run(_test())
