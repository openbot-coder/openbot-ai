"""Google International HTML scraping engine — uses www.google.com."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse, parse_qs, unquote, quote_plus

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


class GoogleScraper(BaseEngine):
    """Google Search HTML scraper — no API key needed.

    Uses ``www.google.com/search`` with English locale.  Handles Google's
    redirect wrapper (``/url?q=<real_url>``) and multiple HTML layout
    variants (2024–2026).
    """

    name = "google"
    region = "international"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        t0 = time.time()
        url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en&gl=us&num={max_results}"

        try:
            async with primp.AsyncClient(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
            elapsed = time.time() - t0
            results = self._parse(resp.text, max_results)
            logger.info("[google] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[google] failed: %s", e)
            return []

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        # Multiple selector patterns for Google's evolving layout
        selector_groups = [
            # Standard layout: div.g containers
            ("div.g", "h3", None),
            # Data-sokoban layout (2024+)
            ("div[data-sokoban-container]", "h3", None),
            # Fallback: any div with an h3 link
            ("div[data-ved]", "h3", None),
        ]

        seen_urls: set[str] = set()

        for container_sel, title_sel, _ in selector_groups:
            containers = soup.select(container_sel)
            if not containers:
                continue

            for item in containers:
                title_el = item.select_one(title_sel)
                if not title_el:
                    continue

                # Title text
                title = title_el.get_text(strip=True)
                if not title:
                    continue

                # URL: the parent <a> tag of h3
                a_tag = title_el.find_parent("a")
                if not a_tag:
                    continue
                raw_href = a_tag.get("href", "")

                # Extract real URL from Google redirect wrapper
                href = self._unwrap_url(raw_href)
                if not href or not href.startswith("http"):
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                # Snippet: try multiple selectors
                snippet = self._extract_snippet(item)

                results.append(SearchResult(
                    title=title, url=href, snippet=snippet, source="google",
                ))

                if len(results) >= max_results:
                    return results

            if results:
                break

        return results

    def _unwrap_url(self, raw_href: str) -> str:
        """Extract the real URL from Google's redirect wrapper.

        Google wraps links as ``/url?q=<real_url>&sa=U&...``
        """
        if not raw_href:
            return ""
        if raw_href.startswith("/url?"):
            parsed = urlparse(raw_href)
            qs = parse_qs(parsed.query)
            if "q" in qs:
                return unquote(qs["q"][0])
        if raw_href.startswith("http"):
            return raw_href
        return ""

    def _extract_snippet(self, item) -> str:
        """Try multiple selectors to extract the snippet text."""
        snippet_selectors = [
            "div.VwiC3b",           # 2024+ layout
            "span.st",              # classic layout
            "div[data-sncf]",       # data-attribute layout
            "div.IsZvec",           # alternate 2024
            "div.lEBKkf",           # another variant
            "span[lang]",           # language-tagged snippet
        ]
        for sel in snippet_selectors:
            el = item.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return text
        return ""
