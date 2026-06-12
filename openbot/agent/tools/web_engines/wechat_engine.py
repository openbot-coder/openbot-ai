"""Sogou WeChat search engine — search WeChat public account articles."""

from __future__ import annotations

import logging
import time
from urllib.parse import quote_plus

import primp
from bs4 import BeautifulSoup

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class WeChatSearch(BaseEngine):
    """Search WeChat public account articles via Sogou (weixin.sogou.com)."""

    name = "wechat"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        t0 = time.time()
        url = f"https://weixin.sogou.com/weixin?type=2&query={quote_plus(query)}"

        try:
            async with primp.AsyncClient(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
                resp = await client.get(url, headers=HEADERS)
                resp.raise_for_status()
            elapsed = time.time() - t0
            results = self._parse(resp.text, max_results)
            logger.info("[wechat] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[wechat] failed: %s", e)
            return []

    def _parse(self, html: str, max_results: int) -> list[SearchResult]:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select("div.txt-box, li"):
            title_el = item.select_one("h3 a, a.account")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")

            # Extract account name
            account_el = item.select_one("div.s-p a, a.account")
            account = account_el.get_text(strip=True) if account_el else ""

            # Extract snippet / summary
            snippet_el = item.select_one("p.txt-info, p.txt")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            # Prepend account name to snippet for context
            if account:
                snippet = f"[{account}] {snippet}" if snippet else f"[{account}]"

            if title and href:
                if href.startswith("/"):
                    href = "https://weixin.sogou.com" + href
                results.append(SearchResult(
                    title=title, url=href, snippet=snippet, source="wechat",
                ))
            if len(results) >= max_results:
                break

        return results
