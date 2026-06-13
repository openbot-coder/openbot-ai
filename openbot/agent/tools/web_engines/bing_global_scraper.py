"""Bing International HTML scraping engine — uses www.bing.com (not cn.bing.com)."""

from __future__ import annotations

import logging
import time
from urllib.parse import quote_plus

import primp
from bs4 import BeautifulSoup

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class BingGlobalScraper(BaseEngine):
    """Bing International — hits www.bing.com with English locale.

    Unlike :class:`BingScraper` (which targets cn.bing.com for Chinese results),
    this engine always uses the international endpoint and English language
    headers, making it suitable for English-language and global queries.
    """

    name = "bing_global"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        t0 = time.time()
        url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en-US&cc=us"

        try:
            async with primp.AsyncClient(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
            elapsed = time.time() - t0
            results = self._parse(resp.text, max_results)
            logger.info("[bing_global] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[bing_global] failed: %s", e)
            return []

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
                    title=title, url=href, snippet=snippet, source="bing_global",
                ))
            if len(results) >= max_results:
                break

        return results
