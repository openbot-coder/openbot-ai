"""360 Search HTML scraping engine — free, good Chinese results."""

from __future__ import annotations

import logging
import time
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class Search360Scraper(BaseEngine):
    """360 Search scraping — good Chinese results, fast in China."""

    name = "360"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        t0 = time.time()
        url = f"https://www.so.com/s?q={quote_plus(query)}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers=HEADERS)
                resp.raise_for_status()
            elapsed = time.time() - t0
            results = self._parse(resp.text, max_results)
            logger.info("[360] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[360] failed: %s", e)
            return []

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select("li.res-list"):
            title_el = item.select_one("h3 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            snippet_el = item.select_one("p.res-desc, .res-rich")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            if title and href:
                results.append(SearchResult(
                    title=title, url=href, snippet=snippet, source="360",
                ))
            if len(results) >= max_results:
                break

        return results
