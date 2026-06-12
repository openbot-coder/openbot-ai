"""News search — multi-source aggregation (Bing News + RSS)."""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Free RSS feeds — no API key needed
_RSS_FEEDS = {
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


class BingNewsEngine(BaseEngine):
    """Bing News scraping — works for both CN and global news."""

    name = "bing_news"

    async def search(self, query: str, max_results: int = 10,
                     region: str = "cn", **kwargs) -> list[SearchResult]:
        t0 = time.time()
        base = "cn.bing.com" if region == "cn" else "www.bing.com"
        url = f"https://{base}/news/search?q={quote_plus(query)}&setlang=zh-CN"

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
            elapsed = time.time() - t0
            results = self._parse(resp.text, max_results)
            logger.info("[bing_news] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[bing_news] failed: %s", e)
            return []

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        results = []
        for item in soup.select("a.title"):
            title = item.get_text(strip=True)
            href = item.get("href", "")
            parent = item.find_parent("div", class_=True)
            snippet = ""
            if parent:
                snippet_el = parent.select_one("div.snippet")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title and href and href.startswith("http"):
                results.append(SearchResult(
                    title=title, url=href, snippet=snippet,
                    source="bing_news", category="news",
                ))
            if len(results) >= max_results:
                break
        return results


class RSSNewsEngine(BaseEngine):
    """RSS feed aggregation — multi-source news from curated feeds."""

    name = "rss_news"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        t0 = time.time()
        all_results = []

        import asyncio
        tasks = [self._fetch_feed(name, url, query)
                 for name, url in _RSS_FEEDS.items()]
        feeds = await asyncio.gather(*tasks, return_exceptions=True)

        for feed_results in feeds:
            if isinstance(feed_results, list):
                all_results.extend(feed_results)

        elapsed = time.time() - t0
        logger.info("[rss_news] %d results in %.2fs", len(all_results), elapsed)
        return all_results[:max_results]

    async def _fetch_feed(self, name: str, url: str, query: str) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
            return self._parse_feed(name, resp.text, query)
        except Exception as e:
            logger.warning("[rss_news:%s] failed: %s", name, e)
            return []

    def _parse_feed(self, name: str, xml_text: str, query: str) -> list[SearchResult]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)

        q_lower = query.lower()
        q_words = [w for w in q_lower.split() if len(w) > 1]

        results = []
        for item in items[:50]:
            title = (item.findtext("title") or
                     item.findtext("atom:title", namespaces=ns) or "")
            link = item.findtext("link") or ""
            if not link:
                link_el = item.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
            desc = (item.findtext("description") or
                    item.findtext("atom:summary", namespaces=ns) or "")
            desc = re.sub(r'<[^>]+>', '', desc)

            text_lower = f"{title} {desc}".lower()
            if q_words and not any(w in text_lower for w in q_words):
                continue  # Skip items with no query keyword match

            results.append(SearchResult(
                title=title.strip(),
                url=link.strip(),
                snippet=desc[:300].strip(),
                source=f"rss_{name}",
                category="news",
            ))

        return results


class NewsSearch(BaseEngine):
    """Unified news search — Bing News + RSS feeds in parallel."""

    name = "news"

    async def search(self, query: str, max_results: int = 10,
                     region: str = "cn", **kwargs) -> list[SearchResult]:
        import asyncio
        bing = BingNewsEngine(timeout=self.timeout)
        rss = RSSNewsEngine(timeout=8.0)

        bing_results, rss_results = await asyncio.gather(
            bing.search(query, max_results=max_results, region=region),
            rss.search(query, max_results=max_results),
            return_exceptions=True,
        )

        all_results = []
        if isinstance(bing_results, list):
            all_results.extend(bing_results)
        if isinstance(rss_results, list):
            all_results.extend(rss_results)

        # Deduplicate by URL
        seen = set()
        deduped = []
        for r in all_results:
            if r.url not in seen:
                seen.add(r.url)
                deduped.append(r)

        return deduped[:max_results]
