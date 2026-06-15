"""Paid search API engines — multi-key round-robin with quota tracking.

Supported providers:
  - baidu_qianfan  — 百度千帆 AI Search API V2 (国内直连)
  - tavily         — Tavily Search API (国内镜像友好)

Each provider inherits from BaseApiEngine which handles:
  - Key rotation (round-robin or least-remaining)
  - Local quota counting (daily reset)
  - Cooldown after consecutive failures
  - Async HTTP via httpx
"""

from __future__ import annotations

import asyncio
import time
from abc import abstractmethod
from dataclasses import dataclass

import httpx
from loguru import logger

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

# ---------------------------------------------------------------------------
# Quota / cooldown tracking per key
# ---------------------------------------------------------------------------

@dataclass
class _KeyState:
    """Runtime state for a single API key."""
    key: str
    quota_used: int = 0
    cooldown_until: float = 0.0
    consecutive_failures: int = 0

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.cooldown_until


class _QuotaTracker:
    """Tracks per-key quota usage and cooldown within a process.

    Not persisted to disk — resets on restart.  For production use,
    replace with Redis or a DB-backed counter.
    """

    def __init__(self, max_queries_per_key: int = 100, cooldown_seconds: float = 60.0):
        self._max = max_queries_per_key
        self._cooldown = cooldown_seconds
        self._states: dict[str, _KeyState] = {}

    def pick_key(self, keys: list[str]) -> str | None:
        """Return the best available key, or None if all exhausted."""
        candidates = [
            self._states.get(k, _KeyState(key=k))
            for k in keys
            if self._states.get(k, _KeyState(key=k)).available
            and not self._is_exhausted(self._states.get(k, _KeyState(key=k)))
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda s: (s.consecutive_failures, s.quota_used))
        return candidates[0].key

    def _is_exhausted(self, state: _KeyState) -> bool:
        return state.quota_used >= self._max

    def record_success(self, key: str) -> None:
        s = self._states.setdefault(key, _KeyState(key=key))
        s.consecutive_failures = 0
        s.quota_used += 1

    def record_failure(self, key: str) -> None:
        s = self._states.setdefault(key, _KeyState(key=key))
        s.consecutive_failures += 1
        if s.consecutive_failures >= 3:
            s.cooldown_until = time.monotonic() + self._cooldown

    def is_exhausted(self, key: str) -> bool:
        s = self._states.get(key, _KeyState(key=key))
        return s.quota_used >= self._max

    def reset_daily(self) -> None:
        for s in self._states.values():
            s.quota_used = 0


# ---------------------------------------------------------------------------
# Base class for API engines
# ---------------------------------------------------------------------------

class BaseApiEngine(BaseEngine):
    """Abstract base for paid search API providers.

    Subclasses implement:
      - _search_with_key(query, max_results, api_key) -> list[SearchResult]
      - _provider_name property
      - _default_endpoint property
    """

    search_type: str = "search"
    _default_endpoint: str = ""
    _default_timeout: float = 10.0

    def __init__(self, timeout: float = 10.0, proxy: str | None = None):
        super().__init__(timeout=timeout, proxy=proxy)
        self._keys: list[str] = []
        self._tracker: _QuotaTracker | None = None

    def configure_keys(self, keys: list[str], max_queries_per_key: int = 100) -> None:
        if not keys:
            raise ValueError("At least one API key required")
        self._keys = [k.strip() for k in keys if k.strip()]
        self._tracker = _QuotaTracker(max_queries_per_key=max_queries_per_key)

    async def search(self, query: str, max_results: int = 10, **kwargs) -> list[SearchResult]:
        if not self._keys or self._tracker is None:
            logger.warning("[{}] no keys configured", self.name)
            return []

        key = self._tracker.pick_key(self._keys)
        if key is None:
            logger.warning("[{}] all keys quota exhausted", self.name)
            return []

        if self._tracker.is_exhausted(key):  # coverage: rare — pick_key filters most cases
            self._tracker.record_failure(key)
            key = self._tracker.pick_key(self._keys)
            if key is None:
                return []  # coverage: requires multi-key all-exhausted scenario

        try:
            results = await asyncio.wait_for(
                self._search_with_key(query, max_results, key),
                timeout=self.timeout,
            )
            self._tracker.record_success(key)
            return results
        except asyncio.TimeoutError:
            self._tracker.record_failure(key)
            logger.warning("[{}] timeout with key ...{}", self.name, key[-4:])
            return []
        except Exception as e:
            self._tracker.record_failure(key)
            logger.warning("[{}] error with key ...{}: {}", self.name, key[-4:], e)
            return []

    async def _search_with_key(self, query: str, max_results: int, api_key: str) -> list[SearchResult]:
        ...

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            proxy=self.proxy,
            timeout=self.timeout,
            follow_redirects=True,
        )


# ---------------------------------------------------------------------------
# Baidu Qianfan AI Search V2
# ---------------------------------------------------------------------------

class BaiduQianfanEngine(BaseApiEngine):
    """百度千帆 AI Search API V2 — 国内直连，百度引擎+AI增强。

    API doc: https://cloud.baidu.com/doc/qianfan/aisearch
    """

    name = "baidu_qianfan"
    _default_endpoint = "https://qianfan.baidubce.com/api/v1/ai_search/v2"
    _default_timeout = 15.0

    def __init__(self, timeout: float = 15.0, proxy: str | None = None):
        super().__init__(timeout=timeout, proxy=proxy)

    def _make_client(self) -> httpx.AsyncClient:  # coverage: trivial wrapper
        headers = {
            "Content-Type": "application/json",
        }
        return httpx.AsyncClient(
            proxy=self.proxy,
            timeout=self.timeout,
            follow_redirects=True,
            headers=headers,
        )

    async def _search_with_key(self, query: str, max_results: int, api_key: str) -> list[SearchResult]:
        # coverage: requires real API — integration test only
        endpoint = self._default_endpoint
        payload = {
            "query": query,
            "num": min(max_results, 10),
        }

        async with self._make_client() as client:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResult] = []
        for item in data.get("results", data.get("data", []))[:max_results]:
            title = item.get("title", "").strip()
            url = item.get("url", item.get("link", "")).strip()
            snippet = item.get("snippet", item.get("summary", item.get("content", ""))).strip()
            if title and url:
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source="baidu_qianfan",
                    category="web",
                    extra={"published": item.get("date", item.get("publish_time", ""))},
                ))
        logger.info("[baidu_qianfan] {} results for query={}", len(results), query[:50])
        return results


# ---------------------------------------------------------------------------
# Tavily Search API
# ---------------------------------------------------------------------------

class TavilyEngine(BaseApiEngine):
    """Tavily Search API — AI Agent 专用，结果质量高。

    API doc: https://docs.tavily.com
    国内可通过镜像源访问（2026版支持国内镜像）。
    """

    name = "tavily"
    _default_endpoint = "https://api.tavily.com/search"
    _default_timeout = 15.0

    def __init__(self, timeout: float = 15.0, proxy: str | None = None, endpoint: str = ""):
        super().__init__(timeout=timeout, proxy=proxy)
        self._endpoint = endpoint or self._default_endpoint

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            proxy=self.proxy,
            timeout=self.timeout,
            follow_redirects=True,
        )

    async def _search_with_key(self, query: str, max_results: int, api_key: str) -> list[SearchResult]:
        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": min(max_results, 10),
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }

        async with self._make_client() as client:
            resp = await client.post(
                self._endpoint,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[SearchResult] = []
        for item in data.get("results", [])[:max_results]:
            title = item.get("title", "").strip()
            url = item.get("url", "").strip()
            snippet = item.get("content", item.get("snippet", "")).strip()
            if title and url:
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source="tavily",
                    category="web",
                    extra={"score": item.get("score", 0)},
                ))
        logger.info("[tavily] {} results for query={}", len(results), query[:50])
        return results
