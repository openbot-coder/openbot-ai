"""Standalone RSS/Atom feed engine — fetches and parses RSS feeds.

Provides a dedicated engine for RSS-based content sources, independent
of the news engine. Supports both RSS 2.0 and Atom formats.

Default feeds are curated Chinese tech/business/news sources. Custom
feeds can be passed via the ``feeds`` kwarg.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from xml.etree import ElementTree as ET

import primp

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Curated free RSS feeds — no API key needed
DEFAULT_FEEDS: dict[str, str] = {
    # Chinese tech/business
    "36kr": "https://36kr.com/feed",
    "少数派": "https://sspai.com/feed",
    "IT之家": "https://www.ithome.com/rss/",
    "钛媒体": "https://www.tmtpost.com/rss.xml",
    "Solidot": "https://www.solidot.org/index.rss",
    "InfoQ CN": "https://www.infoq.cn/feed",
    "OSChina": "https://www.oschina.net/news/rss",
    # English tech
    "HackerNews": "https://hnrss.org/best?count=20",
    "TechCrunch": "https://techcrunch.com/feed/",
    "ArsTechnica": "https://feeds.arstechnica.com/arstechnica/index",
    "TheVerge": "https://www.theverge.com/rss/index.xml",
    "MIT Tech Review": "https://www.technologyreview.com/feed/",
    "Wired": "https://www.wired.com/feed/rss",
    "Lobste.rs": "https://lobste.rs/rss",
    # English general
    "ChinaDaily": "https://www.chinadaily.com.cn/rss/china_rss.xml",
}


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_rss_feed(name: str, xml_text: str, query: str = "") -> list[dict]:
    """Parse RSS 2.0 or Atom feed XML.

    Returns list of dicts with title, url, snippet, published.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("[rss:%s] XML parse error: %s", name, e)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # Try RSS 2.0 first, then Atom
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//atom:entry", ns)

    # Query keyword filtering
    q_words = []
    if query:
        q_lower = query.lower()
        q_words = [w for w in q_lower.split() if len(w) > 1]

    results = []
    for item in items[:50]:
        # RSS 2.0
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""
        pub_date = item.findtext("pubDate") or ""

        # Atom fallback
        if not title:
            title = item.findtext("atom:title", namespaces=ns) or ""
        if not link:
            link_el = item.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
        if not desc:
            desc = item.findtext("atom:summary", namespaces=ns) or ""
        if not pub_date:
            pub_date = item.findtext("atom:updated", namespaces=ns) or ""

        title = title.strip()
        link = link.strip()
        desc = _strip_html(desc)[:300]

        if not title or not link:
            continue

        # Keyword filtering
        if q_words:
            text_lower = f"{title} {desc}".lower()
            if not any(w in text_lower for w in q_words):
                continue

        results.append({
            "title": title,
            "url": link,
            "snippet": desc,
            "published": pub_date,
        })

    return results


class RssEngine(BaseEngine):
    """Standalone RSS/Atom feed engine.

    Fetches from curated default feeds or custom feeds passed via kwargs.
    Ignores query for default mode; if query is provided, filters items
    by keyword matching.
    """

    name = "rss"
    search_type = "non-search"

    async def search(
        self,
        query: str = "",
        max_results: int = 30,
        feeds: dict[str, str] | None = None,
        **kwargs,
    ) -> list[SearchResult]:
        """Fetch and parse RSS feeds.

        Args:
            query: Optional keyword filter across all feeds.
            max_results: Max total results across all feeds.
            feeds: Custom feed dict {name: url}. Default: DEFAULT_FEEDS.
        """
        feed_map = feeds or DEFAULT_FEEDS
        t0 = time.time()

        async with primp.AsyncClient(
            timeout=self.timeout,
            proxy=self.proxy,
            follow_redirects=True,
        ) as client:
            tasks = [
                self._fetch_feed(client, name, url, query)
                for name, url in feed_map.items()
            ]
            feed_results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items: list[dict] = []
        succeeded = 0
        for result in feed_results:
            if isinstance(result, list):
                all_items.extend(result)
                if result:
                    succeeded += 1
            elif isinstance(result, Exception):
                logger.debug("[rss] feed error: %s", result)

        # Deduplicate by URL
        seen: set[str] = set()
        unique: list[dict] = []
        for item in all_items:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)

        elapsed = time.time() - t0
        logger.info(
            "[rss] %d items from %d feeds in %.2fs",
            len(unique), succeeded, elapsed,
        )

        return [
            SearchResult(
                title=item["title"],
                url=item["url"],
                snippet=item["snippet"],
                source="rss",
                category="news",
                published=item.get("published", ""),
            )
            for item in unique[:max_results]
        ]

    async def _fetch_feed(
        self, client: primp.AsyncClient, name: str, url: str, query: str,
    ) -> list[dict]:
        """Fetch and parse a single RSS feed."""
        try:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            return _parse_rss_feed(name, resp.text, query)
        except Exception as e:
            logger.debug("[rss:%s] fetch failed: %s", name, e)
            return []
