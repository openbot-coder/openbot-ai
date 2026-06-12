"""Parsing-based search engines — DuckDuckGo, Brave (fallback HTML scraper)."""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class DuckDuckGoParser(BaseEngine):
    """DuckDuckGo HTML scraper (no API key needed)."""

    name = "duckduckgo"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        import time
        t0 = time.time()
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
            elapsed = time.time() - t0
            results = self._parse(resp.text, max_results)
            logger.info("[duckduckgo] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[duckduckgo] failed: %s", e)
            return []

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for result in soup.select(".result")[:max_results]:
            title_el = result.select_one(".result__title a")
            snippet_el = result.select_one(".result__snippet")
            if not title_el:
                continue
            results.append(SearchResult(
                title=title_el.get_text(strip=True),
                url=title_el.get("href", ""),
                snippet=snippet_el.get_text(strip=True) if snippet_el else "",
                source="duckduckgo",
                category="web",
            ))

        return results


class BraveParser(BaseEngine):
    """Brave Search fallback HTML scraper (no API key needed)."""

    name = "brave"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        import time
        t0 = time.time()
        url = f"https://search.brave.com/search?q={quote_plus(query)}&source=web"

        try:
            async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
            elapsed = time.time() - t0
            results = self._parse(resp.text, max_results)
            logger.info("[brave] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[brave] failed: %s", e)
            return []

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select("div.snippet, div.result")[:max_results]:
            title_el = item.select_one("a.result-header, a.snippet-title, a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            snippet_el = item.select_one("p.snippet-description, div.snippet-description")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            if title and href and href.startswith("http"):
                results.append(SearchResult(
                    title=title, url=href, snippet=snippet,
                    source="brave", category="web",
                ))

        return results


# Backwards-compatibility aliases
DuckDuckGoEngine = DuckDuckGoParser
BraveScraper = BraveParser
