"""Unified data models — single source of truth for all search results.

Defines:
  - SearchResult: unified result from any engine (immutable dataclass)
  - BaseEngine: abstract base for all engines
  - rank field: assigned by aggregator, not engines

All engine-specific models must convert to SearchResult before returning.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

# Maximum number of HTTP redirects to follow per request.  Search engines
# occasionally redirect through CDN nodes or anti-bot challenge pages, so we
# allow a few, but cap to prevent runaway chains from exceeding timeouts.
_MAX_REDIRECTS = 3

# User-Agent used by every engine.  We pretend to be a regular Chrome so we
# don't get served bot-detection pages.
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


@dataclass
class SearchResult:
    """Unified search result — returned by all engines."""
    title: str
    url: str
    snippet: str = ""
    source: str = ""           # engine name (e.g. "bing", "arxiv")
    category: str = ""         # "web" | "news" | "academic" | "github" | "social"
    rank: int = 0              # assigned by aggregator, not engine
    published: str = ""        # ISO date string (optional)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "category": self.category,
            "rank": self.rank,
            "published": self.published,
            "extra": self.extra,
        }


class BaseEngine(ABC):
    """Abstract base for all search engines.

    Subclasses override ``search()``, which returns ``list[SearchResult]``.
    ``health()`` provides a quick connectivity check.
    """

    name: str = "unknown"
    timeout: float = 15.0

    def __init__(self, timeout: float = 15.0, proxy: str | None = None):
        self.timeout = timeout
        self.proxy = proxy

    @abstractmethod
    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        """Search and return unified results."""
        ...

    async def health(self) -> dict[str, Any]:
        """Quick connectivity check."""
        try:
            results = await self.search("test", max_results=1)
            return {"name": self.name, "status": "ok", "count": len(results)}
        except Exception as e:
            return {"name": self.name, "status": "fail", "error": str(e)}

    async def _fetch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """HTTP GET with strict timeout + limited redirects.

        Use this helper in subclasses instead of constructing your own
        ``httpx.AsyncClient``.  It guarantees:

        * The httpx timeout matches ``self.timeout`` exactly (no +2.0
          buffer that would let httpx outlive the asyncio wrapper).
        * At most ``_MAX_REDIRECTS`` redirects are followed.
        * ``asyncio.CancelledError`` is NOT swallowed by the engine's
          try/except — it always propagates so the outer ``wait_for`` can
          terminate the search cleanly.

        Raises:
            httpx.TimeoutException: when the request exceeds ``self.timeout``.
            asyncio.CancelledError: when the outer task is cancelled.
        """
        req_headers = {"User-Agent": _DEFAULT_UA, **(headers or {})}
        # ``proxy=None`` forces httpx to bypass system env proxies, which on
        # Windows can otherwise be picked up automatically and break SSL.
        async with httpx.AsyncClient(
            timeout=self.timeout,
            proxy=self.proxy,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        ) as client:
            return await client.get(url, headers=req_headers, params=params)
