"""Concurrent multi-engine web search orchestrator.

Runs multiple web-search-skills engines in parallel with:
- Per-engine SSRF validation (reuses openbot.security.network)
- Per-engine timeout (default 2s) and total timeout (default 5s)
- URL deduplication + quality filtering
- Prompt injection banner on all results
- Auto-skip unreachable engines (consecutive timeout blacklist)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

from loguru import logger

from openbot.utils.helpers import UNTRUSTED_CONTENT_BANNER as _UNTRUSTED_BANNER


class _ReachabilityCache:
    """Track consecutive failures per engine.  After ``max_failures``
    consecutive timeouts, the engine is blacklisted until it succeeds
    again or ``cooldown_seconds`` elapses.
    """

    def __init__(self, max_failures: int = 2, cooldown_seconds: float = 300.0):
        self._max_failures = max_failures
        self._cooldown = cooldown_seconds
        self._failures: dict[str, dict[str, Any]] = {}

    def is_unreachable(self, name: str) -> bool:
        entry = self._failures.get(name)
        if entry is None:
            return False
        if entry["count"] < self._max_failures:
            return False
        if time.monotonic() - entry["last_fail"] > self._cooldown:
            self._failures.pop(name, None)
            return False
        return True

    def record_success(self, name: str) -> None:
        self._failures.pop(name, None)

    def record_failure(self, name: str) -> None:
        entry = self._failures.get(name)
        if entry is None:
            self._failures[name] = {"count": 1, "last_fail": time.monotonic()}
        else:
            entry["count"] += 1
            entry["last_fail"] = time.monotonic()


_reachability = _ReachabilityCache(max_failures=2, cooldown_seconds=300.0)


_ENGINE_URL_TEMPLATES: dict[str, str] = {
    "bing": "https://cn.bing.com/search?q={q}",
    "bing_global": "https://www.bing.com/search?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "sogou": "https://www.sogou.com/web?query={q}",
    "baidu": "https://www.baidu.com/s?wd={q}",
    "360": "https://www.so.com/s?q={q}",
    "duckduckgo": "https://html.duckduckgo.com/html/?q={q}",
    "brave": "https://search.brave.com/search?q={q}",
    "news": "https://cn.bing.com/news/search?q={q}",
    "academic": "https://export.arxiv.org/api/query?search_query=all:{q}",
    "github": "https://api.github.com/search/repositories?q={q}",
    "wechat": "https://weixin.sogou.com/weixin?type=2&query={q}",
    "baidu_web_search": "https://qianfan.baidubce.com/v2/ai_search/web_search",
    "baidu_ai_search": "https://qianfan.baidubce.com/v2/ai_search/chat/completions",
    "tavily": "https://api.tavily.com/search",
}


_api_engine_cache: dict[str, Any] = {}


def _build_engine_instances(
    timeout: float = 10.0,
    proxy: str | None = None,
    api_keys: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Create one instance per engine.  Called once per search.

    API engines are cached at module level to preserve quota tracking state
    across multiple search calls.  Scraper engines are created fresh each time.
    """
    from openbot.agent.tools.web_engines import (
        AcademicSearch, BaiduAISearchEngine, BaiduScraper,
        BaiduWebSearchEngine, BingGlobalScraper, BingScraper,
        BraveParser, DuckDuckGoParser, GitHubEngine, GoogleScraper,
        HotlistEngine, NewsSearch, RssEngine, Search360Scraper,
        SogouScraper, TavilyEngine, WeChatSearch,
    )

    engines: dict[str, Any] = {
        "bing": BingScraper(timeout=timeout, proxy=proxy),
        "bing_global": BingGlobalScraper(timeout=timeout, proxy=proxy),
        "google": GoogleScraper(timeout=timeout, proxy=proxy),
        "sogou": SogouScraper(timeout=timeout, proxy=proxy),
        "baidu": BaiduScraper(timeout=timeout, proxy=proxy),
        "360": Search360Scraper(timeout=timeout, proxy=proxy),
        "duckduckgo": DuckDuckGoParser(timeout=timeout, proxy=proxy),
        "brave": BraveParser(timeout=timeout, proxy=proxy),
        "news": NewsSearch(timeout=timeout, proxy=proxy),
        "academic": AcademicSearch(timeout=timeout, proxy=proxy),
        "github": GitHubEngine(timeout=timeout, proxy=proxy),
        "wechat": WeChatSearch(timeout=timeout, proxy=proxy),
        "hotlist": HotlistEngine(timeout=timeout, proxy=proxy),
        "rss": RssEngine(timeout=timeout, proxy=proxy),
    }

    if api_keys:
        bws_keys = api_keys.get("baidu_web_search")
        if bws_keys:
            if "baidu_web_search" in _api_engine_cache:
                eng = _api_engine_cache["baidu_web_search"]
                eng.timeout = timeout
                eng.proxy = proxy
            else:
                eng = BaiduWebSearchEngine(timeout=timeout, proxy=proxy)
                eng.configure_keys(bws_keys)
                _api_engine_cache["baidu_web_search"] = eng
            engines["baidu_web_search"] = eng

        bai_keys = api_keys.get("baidu_ai_search")
        if bai_keys:
            if "baidu_ai_search" in _api_engine_cache:
                eng = _api_engine_cache["baidu_ai_search"]
                eng.timeout = timeout
                eng.proxy = proxy
            else:
                eng = BaiduAISearchEngine(timeout=timeout, proxy=proxy)
                eng.configure_keys(bai_keys)
                _api_engine_cache["baidu_ai_search"] = eng
            engines["baidu_ai_search"] = eng

        tv_keys = api_keys.get("tavily")
        if tv_keys:
            if "tavily" in _api_engine_cache:
                eng = _api_engine_cache["tavily"]
                eng.timeout = timeout
                eng.proxy = proxy
            else:
                eng = TavilyEngine(timeout=timeout, proxy=proxy)
                eng.configure_keys(tv_keys)
                _api_engine_cache["tavily"] = eng
            engines["tavily"] = eng

    return engines


ENGINE_GROUPS: dict[str, list[str]] = {
    "local": ["bing", "sogou", "baidu", "360", "wechat"],
    "global": ["bing", "sogou", "baidu", "360", "bing_global", "google", "duckduckgo", "brave"],
    "web_metasearch": [],
    "news": ["news"],
    "academic": ["academic"],
    "github": ["github"],
    "wechat": ["wechat"],
    "hotlist": ["hotlist"],
    "rss": ["rss"],
    "non-search": ["hotlist", "rss"],
    "baidu_web_search": ["baidu_web_search"],
    "baidu_ai_search": ["baidu_ai_search"],
    "tavily": ["tavily"],
    "api": ["baidu_web_search", "baidu_ai_search", "tavily"],
    "all": [
        "bing", "sogou", "baidu", "360", "bing_global", "google",
        "duckduckgo", "brave", "news", "academic", "github", "wechat",
        "hotlist", "rss", "baidu_web_search", "baidu_ai_search", "tavily",
    ],
}


def _ssrf_check(engine_name: str, query: str) -> tuple[bool, str]:
    """Validate the search-engine URL before calling the engine."""
    template = _ENGINE_URL_TEMPLATES.get(engine_name)
    if not template:
        return True, ""
    url = template.format(q=quote_plus(query))
    try:
        from openbot.security.network import validate_url_target
        return validate_url_target(url)
    except ImportError:
        logger.warning("SSRF module unavailable, skipping check for {}", engine_name)
        return True, ""


@dataclass
class _EngineResult:
    engine: str
    results: list[Any] = field(default_factory=list)
    error: str | None = None
    timed_out: bool = False
    duration_ms: int = 0


@dataclass
class SearchStats:
    total_engines: int = 0
    succeeded: int = 0
    failed: list[str] = field(default_factory=list)
    timed_out: list[str] = field(default_factory=list)
    total_raw: int = 0
    deduplicated: int = 0
    duration_ms: int = 0
    per_engine: dict[str, dict[str, Any]] = field(default_factory=dict)


async def _run_one_engine(
    engine_name: str, engine: Any, query: str,
    max_results: int, engine_timeout: float,
) -> _EngineResult:
    t0 = time.monotonic()

    if _reachability.is_unreachable(engine_name):
        logger.debug("[{}] skipped - unreachable (blacklisted)", engine_name)
        return _EngineResult(engine=engine_name, error="skipped: unreachable", duration_ms=0)

    ok, err = _ssrf_check(engine_name, query)
    if not ok:
        return _EngineResult(
            engine=engine_name, error=f"SSRF blocked: {err}",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    hard_timeout = engine_timeout * 2
    try:
        kwargs: dict[str, Any] = {"max_results": max_results}
        if engine_name == "bing":
            kwargs["region"] = "cn"
        results = await asyncio.wait_for(
            engine.search(query, **kwargs), timeout=hard_timeout,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        _reachability.record_success(engine_name)
        return _EngineResult(engine=engine_name, results=results or [], duration_ms=elapsed)
    except asyncio.TimeoutError:
        _reachability.record_failure(engine_name)
        return _EngineResult(
            engine=engine_name, timed_out=True,
            error=f"timeout after {hard_timeout:.0f}s",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:
        _reachability.record_failure(engine_name)
        return _EngineResult(
            engine=engine_name, error=str(exc),
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        url = _normalize_url(item.get("url", ""))
        title = item.get("title", "").strip()
        if not url or url in seen:
            continue
        if len(title) < 2:
            continue
        seen.add(url)
        out.append(item)
    return out


async def concurrent_search(
    query: str,
    region: str = "local",
    max_results: int = 5,
    engine_timeout: float = 2.0,
    total_timeout: float = 5.0,
    proxy: str | None = None,
    engines: list[str] | None = None,
    api_keys: dict[str, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], SearchStats]:
    """Run multiple search engines concurrently and merge results."""
    t0 = time.monotonic()

    if engines is not None:
        engine_names = engines
    else:
        engine_names = ENGINE_GROUPS.get(region, ENGINE_GROUPS["local"])

    all_engines = _build_engine_instances(
        timeout=engine_timeout, proxy=proxy, api_keys=api_keys,
    )

    engine_names = [
        n for n in engine_names
        if all_engines.get(n) is not None and all_engines[n].search_type != "non-search"
    ]

    pending: dict[str, asyncio.Task[_EngineResult]] = {}
    for name in engine_names:
        eng = all_engines.get(name)
        if eng is None:
            logger.warning("Unknown engine: {}", name)
            continue
        pending[name] = asyncio.create_task(
            _run_one_engine(name, eng, query, max_results, engine_timeout)
        )

    stats = SearchStats(total_engines=len(pending))
    completed: dict[str, _EngineResult] = {}
    deadline = time.monotonic() + total_timeout

    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            for name, task in pending.items():
                task.cancel()
                completed[name] = _EngineResult(engine=name, timed_out=True, error="total timeout")
            if pending:
                drain, _ = await asyncio.wait(list(pending.values()), timeout=0.5)
                for t in drain:
                    try:
                        t.result()
                    except (asyncio.CancelledError, Exception):
                        pass
            break

        done, _ = await asyncio.wait(
            list(pending.values()), timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done:
            for name, t in list(pending.items()):
                if t is task:
                    try:
                        result = task.result()
                    except Exception as exc:
                        result = _EngineResult(engine=name, error=str(exc))
                    completed[name] = result
                    del pending[name]
                    break

    all_items: list[dict[str, Any]] = []
    for name in engine_names:
        er = completed.get(name)
        if er is None:
            er = _EngineResult(engine=name, error="not started")

        stats.per_engine[name] = {
            "duration_ms": er.duration_ms,
            "results": len(er.results),
            "error": er.error,
            "timed_out": er.timed_out,
        }

        if er.timed_out:
            stats.timed_out.append(name)
            logger.debug("[{}] timed out", name)
        elif er.error:
            stats.failed.append(name)
            logger.debug("[{}] error: {}", name, er.error)
        else:
            stats.succeeded += 1
            for r in er.results:
                item = {
                    "title": getattr(r, "title", "") or "",
                    "url": getattr(r, "url", "") or "",
                    "snippet": getattr(r, "snippet", "") or "",
                    "source": getattr(r, "source", name),
                    "category": getattr(r, "category", "") or region,
                }
                extra = getattr(r, "extra", None)
                if extra:
                    item["extra"] = extra
                all_items.append(item)

    stats.total_raw = len(all_items)
    deduped = _deduplicate(all_items)
    stats.deduplicated = len(deduped)

    for i, item in enumerate(deduped):
        item["rank"] = i + 1

    stats.duration_ms = int((time.monotonic() - t0) * 1000)
    return deduped, stats


def format_concurrent_results(
    query: str, items: list[dict[str, Any]],
    stats: SearchStats, max_display: int = 10,
) -> str:
    if not items:
        text = f"No results for: {query}"
    else:
        lines = [f"Results for: {query}\n"]
        for item in items[:max_display]:
            title = item.get("title", "").strip()
            url = item.get("url", "")
            snippet = item.get("snippet", "").strip()
            source = item.get("source", "")
            rank = item.get("rank", 0)
            lines.append(f"{rank}. {title}  [{source}]\n   {url}")
            if snippet:
                lines.append(f"   {snippet}")
        text = "\n".join(lines)

    text = f"{_UNTRUSTED_BANNER}\n\n{text}"

    parts = [f"{stats.succeeded}/{stats.total_engines} ok"]
    if stats.timed_out:
        parts.append(f"timed out: {','.join(stats.timed_out)}")
    if stats.failed:
        parts.append(f"failed: {','.join(stats.failed)}")
    parts.append(f"{stats.duration_ms}ms")
    text += f"\n\n[Engines: {' | '.join(parts)}]"
    return text
