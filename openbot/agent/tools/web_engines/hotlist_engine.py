"""Hotlist engine — fetches trending data from Chinese platforms.

Each platform's public API returns JSON directly (no auth needed).
Results are merged and deduplicated by title.

Supported platforms: toutiao, weibo, baidu, bilibili, v2ex, juejin, cls.
"""

from __future__ import annotations

import json
import logging
import re
import time

import primp

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Platform-specific API endpoints and parsers
_PLATFORMS = {
    "toutiao": {
        "url": "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
        "headers": _HEADERS,
    },
    "weibo": {
        "url": "https://weibo.com/ajax/side/hotSearch",
        "headers": {**_HEADERS, "Referer": "https://weibo.com"},
    },
    "baidu": {
        "url": "https://top.baidu.com/board?tab=realtime",
        "headers": _HEADERS,
    },
    "bilibili": {
        "url": "https://api.bilibili.com/x/web-interface/popular",
        "headers": _HEADERS,
    },
    "v2ex": {
        "url": "https://www.v2ex.com/api/topics/hot.json",
        "headers": _HEADERS,
    },
    "juejin": {
        "url": "https://api.juejin.cn/content_api/v1/content/article_rank?category_id=1&type=hot",
        "headers": _HEADERS,
    },
    "cls": {
        "url": "https://www.cls.cn/api/cache?app=CailianpressWeb&name=telegraph&os=web&sv=8.7.9",
        "headers": {**_HEADERS, "Referer": "https://www.cls.cn/"},
    },
}


def _parse_toutiao(data: dict) -> list[dict]:
    """Parse Toutiao hot board response."""
    results = []
    for item in data.get("data", []):
        title = item.get("Title", "").strip()
        if not title:
            continue
        hot_value = item.get("HotValue", "0")
        url = f"https://www.toutiao.com/trending/{item.get('ClusterIdStr', '')}/"
        results.append({
            "title": title,
            "url": url,
            "snippet": item.get("LabelDesc", ""),
            "hot_value": int(hot_value) if hot_value.isdigit() else 0,
            "platform": "toutiao",
        })
    return results


def _parse_weibo(data: dict) -> list[dict]:
    """Parse Weibo hot search response."""
    results = []
    realtime = data.get("data", {}).get("realtime", [])
    for item in realtime:
        note = item.get("note", "").strip()
        if not note:
            continue
        raw_hot = item.get("num", 0)
        word = item.get("word", note)
        url = f"https://s.weibo.com/weibo?q=%23{word}%23"
        results.append({
            "title": note,
            "url": url,
            "snippet": item.get("category", ""),
            "hot_value": int(raw_hot) if raw_hot else 0,
            "platform": "weibo",
        })
    return results


def _parse_baidu(html: str) -> list[dict]:
    """Parse Baidu hot board HTML (JSON embedded in comment)."""
    match = re.search(r"<!--s-data:(.*?)-->", html, re.DOTALL)
    if not match:
        return []
    try:
        bdata = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    results = []
    cards = bdata.get("data", {}).get("cards", [])
    for card in cards:
        for item in card.get("content", []):
            word = item.get("word", "").strip()
            if not word:
                continue
            url = item.get("url") or item.get("rawUrl", "")
            hot_score = item.get("hotScore", "0")
            desc = item.get("desc", "")
            results.append({
                "title": word,
                "url": url,
                "snippet": desc[:100] if desc else "",
                "hot_value": int(hot_score) if str(hot_score).isdigit() else 0,
                "platform": "baidu",
            })
    return results


def _parse_bilibili(data: dict) -> list[dict]:
    """Parse Bilibili popular videos response."""
    results = []
    for item in data.get("data", {}).get("list", []):
        title = item.get("title", "").strip()
        if not title:
            continue
        bvid = item.get("bvid", "")
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        stat = item.get("stat", {})
        view = stat.get("view", 0)
        like = stat.get("like", 0)
        owner = item.get("owner", {}).get("name", "")
        desc = item.get("desc", "")[:100] if item.get("desc") else ""
        snippet = f"{owner} | 👁{view} ❤{like}" if owner else f"👁{view} ❤{like}"
        if desc:
            snippet += f" | {desc}"
        results.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "hot_value": view,
            "platform": "bilibili",
        })
    return results


def _parse_v2ex(data: list) -> list[dict]:
    """Parse V2EX hot topics response."""
    results = []
    for item in data:
        title = item.get("title", "").strip()
        if not title:
            continue
        url = item.get("url", "")
        replies = item.get("replies", 0)
        member = item.get("member", {}).get("username", "")
        results.append({
            "title": title,
            "url": url,
            "snippet": f"@{member} | 💬{replies}" if member else f"💬{replies}",
            "hot_value": replies,
            "platform": "v2ex",
        })
    return results


def _parse_juejin(data: dict | list) -> list[dict]:
    """Parse Juejin article rank response."""
    results = []
    items = data if isinstance(data, list) else data.get("data", [])
    for item in items:
        content = item.get("content", {})
        title = content.get("title", "").strip()
        if not title:
            continue
        article_id = content.get("article_id", "")
        url = f"https://juejin.cn/post/{article_id}" if article_id else ""
        digg_count = content.get("digg_count", 0)
        view_count = content.get("view_count", 0)
        author = content.get("author_user_info", {}).get("user_name", "")
        results.append({
            "title": title,
            "url": url,
            "snippet": f"@{author} | 👁{view_count} 👍{digg_count}" if author else f"👁{view_count} 👍{digg_count}",
            "hot_value": digg_count + view_count,
            "platform": "juejin",
        })
    return results


def _parse_cls(data: dict) -> list[dict]:
    """Parse CLS (财联社) telegraph response."""
    results = []
    roll_data = data.get("data", {}).get("roll_data", [])
    for item in roll_data:
        title = item.get("title", "").strip()
        content = item.get("content", "").strip()
        if not title and not content:
            continue
        # Use title as display, content as snippet
        display_title = title if title else content[:50]
        ctime = item.get("ctime", 0)
        level = item.get("level", "C")
        item_id = item.get("id", "")
        url = f"https://www.cls.cn/telegraph/{item_id}" if item_id else ""
        reading = item.get("reading_num", 0)
        # Level mapping: A=重大, B=一般, C=次要
        level_label = {"A": "🔴重大", "B": "🟡一般", "C": "⚪次要"}.get(level, level)
        snippet = f"{level_label} | 👁{reading:,}" if reading else level_label
        if content and content != title:
            snippet += f" | {content[:80]}"
        results.append({
            "title": display_title,
            "url": url,
            "snippet": snippet,
            "hot_value": reading,
            "platform": "cls",
        })
    return results


class HotlistEngine(BaseEngine):
    """Fetches trending/hot-list data from Chinese platforms.

    Unlike search engines, this ignores the query and returns the current
    top trending items.  The ``platforms`` kwarg controls which platforms
    to query (default: all three).
    """

    name = "hotlist"
    search_type = "non-search"

    async def search(
        self,
        query: str = "",
        max_results: int = 50,
        platforms: str | list[str] | None = None,
        **kwargs,
    ) -> list[SearchResult]:
        """Fetch hot lists from specified platforms.

        Args:
            query: Ignored (hot lists are not query-based).
            max_results: Max results per platform.
            platforms: Comma-separated string or list of platform names.
                       Default: ["toutiao", "weibo", "baidu"].
        """
        if platforms is None:
            platform_list = ["toutiao", "weibo", "baidu", "bilibili", "v2ex", "juejin", "cls"]
        elif isinstance(platforms, str):
            platform_list = [p.strip() for p in platforms.split(",")]
        else:
            platform_list = platforms

        all_items: list[dict] = []
        t0 = time.time()

        async with primp.AsyncClient(
            timeout=self.timeout,
            proxy=self.proxy,
            follow_redirects=True,
        ) as client:
            for platform in platform_list:
                cfg = _PLATFORMS.get(platform)
                if cfg is None:
                    logger.warning("Unknown hotlist platform: %s", platform)
                    continue
                try:
                    method = cfg.get("method", "GET")
                    body = cfg.get("body")
                    if method == "POST" and body:
                        resp = await client.post(
                            cfg["url"],
                            headers=cfg["headers"],
                            content=body.encode("utf-8") if isinstance(body, str) else body,
                        )
                    else:
                        resp = await client.get(cfg["url"], headers=cfg["headers"])
                    resp.raise_for_status()

                    if platform == "baidu":
                        items = _parse_baidu(resp.text)
                    elif platform == "bilibili":
                        items = _parse_bilibili(resp.json())
                    elif platform == "v2ex":
                        items = _parse_v2ex(resp.json())
                    elif platform == "juejin":
                        items = _parse_juejin(resp.json())
                    elif platform == "toutiao":
                        items = _parse_toutiao(resp.json())
                    elif platform == "cls":
                        items = _parse_cls(resp.json())
                    else:
                        items = _parse_weibo(resp.json())

                    all_items.extend(items[:max_results])
                    logger.debug(
                        "[hotlist] %s returned %d items (%.0fms)",
                        platform,
                        len(items),
                        (time.time() - t0) * 1000,
                    )
                except Exception as e:
                    logger.warning("[hotlist] %s failed: %s", platform, e)

        # Deduplicate by title
        seen: set[str] = set()
        unique: list[dict] = []
        for item in all_items:
            key = item["title"].lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)

        # Convert to SearchResult, sorted by hot_value desc
        unique.sort(key=lambda x: x.get("hot_value", 0), reverse=True)

        results = []
        for rank, item in enumerate(unique, 1):
            results.append(SearchResult(
                title=item["title"],
                url=item["url"],
                snippet=item.get("snippet", ""),
                source="hotlist",
                category="hotlist",
                rank=rank,
                extra={
                    "platform": item.get("platform", ""),
                    "hot_value": item.get("hot_value", 0),
                },
            ))

        logger.info(
            "[hotlist] total: %d unique items from %d platforms (%.0fms)",
            len(results),
            len(platform_list),
            (time.time() - t0) * 1000,
        )
        return results
