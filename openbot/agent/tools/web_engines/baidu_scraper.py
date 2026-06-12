"""Baidu HTML scraping engine — free, fastest in China, best Chinese results."""

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
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class BaiduScraper(BaseEngine):
    """Baidu web scraping — fastest in China, best Chinese content."""

    name = "baidu"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        t0 = time.time()
        url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={max_results}"

        try:
            resp = await self._fetch(url, headers=HEADERS)
            resp.raise_for_status()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[baidu] failed: %s", e)
            return []

        elapsed = time.time() - t0
        results = self._parse(resp.text, max_results)
        logger.info("[baidu] %d results in %.2fs", len(results), elapsed)
        return results

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select("div.result, div.c-container"):
            title_el = item.select_one("h3 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            snippet_el = item.select_one(
                "span.content-right_8Zs40, div.c-abstract, "
                "span[class*='content'], div[class*='abstract']"
            )
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            if title and href:
                results.append(SearchResult(
                    title=title, url=href, snippet=snippet, source="baidu",
                ))
            if len(results) >= max_results:
                break

        return results
