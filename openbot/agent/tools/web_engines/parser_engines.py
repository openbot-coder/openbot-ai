"""Parsing-based search engines — DuckDuckGo, Brave (fallback HTML scraper)."""

from __future__ import annotations

import logging
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
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class DuckDuckGoParser(BaseEngine):
    """DuckDuckGo HTML scraper (no API key needed).

    Tries ``html.duckduckgo.com`` first, falls back to ``lite.duckduckgo.com``
    if the primary endpoint is unreachable or returns no results.
    """

    name = "duckduckgo"
    region = "international"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        import time
        t0 = time.time()

        # Primary endpoint
        results = await self._try_endpoint(
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            max_results,
        )
        if not results:
            # Fallback: lite endpoint (simpler HTML, often more reachable)
            results = await self._try_endpoint(
                f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}",
                max_results,
            )

        elapsed = time.time() - t0
        logger.info("[duckduckgo] %d results in %.2fs", len(results), elapsed)
        return results

    async def _try_endpoint(self, url: str, max_results: int) -> list[SearchResult]:
        try:
            async with primp.AsyncClient(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
            return self._parse(resp.text, max_results)
        except Exception as e:
            logger.warning("[duckduckgo] %s failed: %s", url.split("?")[0], e)
            return []

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        # Try multiple selector patterns for different DDG HTML layouts
        selectors = [
            # html.duckduckgo.com layout
            (".result", ".result__title a", ".result__snippet"),
            # lite.duckduckgo.com layout
            (".result-link", ".result-link", ".result-snippet"),
            # Broader fallback
            (".web-result", ".web-result__title a", ".web-result__snippet"),
            # Fallback: any link + paragraph combo
            (".results_links", "a", "p"),
        ]

        for container_sel, title_sel, snippet_sel in selectors:
            containers = soup.select(container_sel)
            if not containers:
                continue

            for result in containers[:max_results]:
                title_el = result.select_one(title_sel) if title_sel else None
                snippet_el = result.select_one(snippet_sel) if snippet_sel else None

                if not title_el:
                    # For lite layout, the container itself might be an <a>
                    if result.name == "a" and result.get("href", "").startswith("http"):
                        title = result.get_text(strip=True)
                        href = result.get("href", "")
                    else:
                        continue
                else:
                    title = title_el.get_text(strip=True)
                    href = title_el.get("href", "")

                if not title:
                    continue

                # DDG wraps links through redirect URL — extract real URL
                if "//duckduckgo.com/l/" in href:
                    from urllib.parse import urlparse, parse_qs, unquote
                    parsed = urlparse(href)
                    qs = parse_qs(parsed.query)
                    if "uddg" in qs:
                        href = unquote(qs["uddg"][0])

                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                results.append(SearchResult(
                    title=title, url=href, snippet=snippet,
                    source="duckduckgo", category="web",
                ))

            if results:
                break

        return results


class BraveParser(BaseEngine):
    """Brave Search fallback HTML scraper (no API key needed).

    Uses multiple selector patterns to handle Brave's evolving HTML structure.
    """

    name = "brave"
    region = "international"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        import time
        t0 = time.time()
        url = f"https://search.brave.com/search?q={quote_plus(query)}&source=web"

        try:
            async with primp.AsyncClient(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
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

        # Multiple selector patterns for Brave's evolving layout
        selector_groups = [
            # 2025+ layout: div.snippet with nested a.snippet-title
            ("div.snippet", "a.snippet-title", "p.snippet-description, div.snippet-description"),
            # Alternative: div[data-type="web"] with nested result
            ("div[data-type='web']", "a.result-header", "p.snippet-description"),
            # Fallback: any div with result-like classes
            ("div.result, div.result-item", "a.result-header, a", "p, div.snippet-description"),
            # Last resort: look for Brave's data attributes
            ("section[data-type='web'] div, div[data-type='web'] div", "a", "p"),
        ]

        for container_sel, title_sel, snippet_sel in selector_groups:
            containers = soup.select(container_sel)
            if not containers:
                continue

            for item in containers[:max_results * 2]:  # scan more to find valid ones
                title_el = item.select_one(title_sel)
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                snippet_el = item.select_one(snippet_sel)
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                if title and href and href.startswith("http"):
                    results.append(SearchResult(
                        title=title, url=href, snippet=snippet,
                        source="brave", category="web",
                    ))

                if len(results) >= max_results:
                    break

            if results:
                break

        return results


# Backwards-compatibility aliases
DuckDuckGoEngine = DuckDuckGoParser
BraveScraper = BraveParser
