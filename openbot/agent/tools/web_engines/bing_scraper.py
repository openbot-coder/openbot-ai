"""Bing HTML scraping engine — free, no API key."""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class BingScraper(BaseEngine):
    """Bing web scraping — fast, good English results."""

    name = "bing"

    async def search(self, query: str, max_results: int = 10,
                     region: str = "global", **kwargs) -> list[SearchResult]:
        t0 = time.time()
        url = f"https://cn.bing.com/search?q={quote_plus(query)}&setlang=zh-CN"
        if region == "global":
            url = f"https://www.bing.com/search?q={quote_plus(query)}"

        try:
            resp = await self._fetch(url, headers=HEADERS)
            resp.raise_for_status()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[bing] failed: %s", e)
            return []

        elapsed = time.time() - t0
        results = self._parse(resp.text, max_results)
        logger.info("[bing] %d results in %.2fs", len(results), elapsed)
        return results

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select("li.b_algo"):
            title_el = item.select_one("h2 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            snippet_el = item.select_one("p, .b_caption p")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            if title and href:
                results.append(SearchResult(
                    title=title, url=href, snippet=snippet, source="bing",
                ))
            if len(results) >= max_results:
                break

        return results
