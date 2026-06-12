"""DDGS metasearch engine — wraps the ``ddgs`` PyPI package.

Provides multi-backend text, news, and image search with automatic
backend rotation.  Works best when at least one of bing/yandex is
reachable from the deployment server.

Backends available from mainland China:
  - text: bing ✅, yandex ✅
  - news: bing (limited)
  - images: bing ✅

Install: ``pip install ddgs``
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult


class DDGSEngine(BaseEngine):
    """Metasearch engine backed by the ``ddgs`` library.

    Uses ``backend="auto"`` by default, which tries bing → yandex → others
    in sequence.  You can also specify a single backend or a comma-delimited
    list (e.g. ``"bing,yandex"``).

    For ``category="news"`` the ``.news()`` method is used; for images
    ``.images()``; everything else falls through to ``.text()``.
    """

    name = "ddgs"

    def __init__(
        self,
        timeout: float = 15.0,
        proxy: str | None = None,
        backend: str = "auto",
        region: str = "us-en",
    ):
        super().__init__(timeout=timeout, proxy=proxy)
        self._backend = backend
        self._region = region

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        max_results: int = 10,
        *,
        category: str = "web",
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Run a metasearch query and return unified results.

        Parameters
        ----------
        query : str
            The search query.
        max_results : int
            Maximum results to return.
        category : str
            ``"web"`` → ``.text()``, ``"news"`` → ``.news()``,
            ``"images"`` → ``.images()``.
        """
        t0 = time.monotonic()

        try:
            if category == "news":
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, self._search_news, query, max_results,
                )
            elif category == "images":
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, self._search_images, query, max_results,
                )
            else:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, self._search_text, query, max_results,
                )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            logger.warning("[ddgs] search failed ({}ms): {}", elapsed, exc)
            return []

        elapsed = (time.monotonic() - t0) * 1000
        logger.info("[ddgs] {} results in {:.0f}ms (category={})", len(raw), elapsed, category)
        return raw

    # ------------------------------------------------------------------
    # Private helpers — run in executor (blocking ddgs calls)
    # ------------------------------------------------------------------

    def _get_ddgs(self):
        """Lazy-import and instantiate DDGS."""
        from ddgs import DDGS
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return DDGS(**kwargs)

    def _search_text(self, query: str, max_results: int) -> list[SearchResult]:
        ddgs = self._get_ddgs()
        try:
            items = ddgs.text(
                query,
                max_results=max_results,
                backend=self._backend,
                region=self._region,
            )
        except Exception:
            # If auto fails, try bing directly
            if self._backend != "bing":
                items = ddgs.text(
                    query,
                    max_results=max_results,
                    backend="bing",
                    region=self._region,
                )
            else:
                raise
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("href", ""),
                snippet=item.get("body", ""),
                source=f"ddgs/{item.get('backend', self._backend)}",
                category="web",
            )
            for item in items
        ]

    def _search_news(self, query: str, max_results: int) -> list[SearchResult]:
        ddgs = self._get_ddgs()
        items = ddgs.news(
            query,
            max_results=max_results,
            backend=self._backend,
            region=self._region,
        )
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("body", ""),
                source=f"ddgs/{item.get('backend', self._backend)}",
                category="news",
                published=item.get("date", ""),
                extra={"image": item.get("image", "")},
            )
            for item in items
        ]

    def _search_images(self, query: str, max_results: int) -> list[SearchResult]:
        ddgs = self._get_ddgs()
        items = ddgs.images(
            query,
            max_results=max_results,
            backend=self._backend,
            region=self._region,
        )
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("thumbnail", ""),
                source=f"ddgs/{item.get('backend', self._backend)}",
                category="images",
                extra={
                    "image": item.get("image", ""),
                    "height": item.get("height", 0),
                    "width": item.get("width", 0),
                },
            )
            for item in items
        ]
