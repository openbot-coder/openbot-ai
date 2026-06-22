"""Web tools: web_search and web_fetch."""

from __future__ import annotations

import asyncio
import html
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import cachetools
import primp
from loguru import logger
from pydantic import Field

from openbot.agent.tools.base import Tool, tool_parameters
from openbot.agent.tools.schema import (
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from openbot.config.schema import Base
from openbot.utils.helpers import UNTRUSTED_CONTENT_BANNER, build_image_content_blocks

# Shared constants
_DEFAULT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5  # Limit redirects to prevent DoS attacks


class WebSearchConfig(Base):
    """Web search configuration."""
    enable: bool = True
    max_results: int = 5
    engines: list[str] = Field(
        default_factory=lambda: [
            "bing", "sogou", "baidu", "360", "duckduckgo", "brave",
        ]
    )
    engine_timeout: float = 2.0
    total_timeout: float = 5.0
    api_keys: dict[str, list[str]] = Field(
        default_factory=dict,
        description='Paid API engine keys: {"baidu_web_search": ["key1"], "baidu_ai_search": ["key2"], "tavily": [...]}',
    )
    # Search mode: 'fast' (free scrapers) or 'quality' (paid API engines)
    mode: str = "fast"

    @property
    def effective_engines(self) -> list[str]:
        """Return engines based on mode. Mode 'fast' uses free scrapers,
        mode 'quality' uses paid API engines."""
        if self.mode == "quality":
            return [e for e in self.engines if e in (
                "tavily", "baidu_web_search", "baidu_ai_search",
            )]
        # Default: fast mode (free scrapers)
        return [e for e in self.engines if e not in (
            "tavily", "baidu_web_search", "baidu_ai_search",
        )]


class WebFetchConfig(Base):
    """Web fetch tool configuration."""
    connect_timeout: float = 3.0
    read_timeout: float = 2.0
    max_concurrency: int = 20
    cache_ttl: int = 300


class WebToolsConfig(Base):
    """Web tools configuration."""
    enable: bool = True
    proxy: str | None = None
    user_agent: str | None = None
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    fetch: WebFetchConfig = Field(default_factory=WebFetchConfig)


def _strip_tags(text: str) -> str:
    text = re.sub(r'<script[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https'):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        return True, ""
    except Exception as e:
        return False, str(e)


def _validate_url_safe(url: str) -> tuple[bool, str]:
    from openbot.security.network import validate_url_target
    return validate_url_target(url)


async def _fetch_with_safe_redirects(
    client: primp.AsyncClient,
    url: str,
    headers: dict[str, str] | None = None,
) -> tuple[primp.AsyncResponse | None, str | None]:
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        is_valid, error_msg = _validate_url_safe(current_url)
        if not is_valid:
            return None, f"Redirect blocked: {error_msg}"
        response = await client.get(current_url, headers=headers)
        is_redirect = 300 <= response.status_code < 400
        if not is_redirect:
            return response, None
        location = response.headers.get("location")
        if not location:
            return response, None
        next_url = urljoin(str(response.url), location)
        await response.aclose()
        current_url = next_url
    return None, f"Too many redirects: exceeded limit of {MAX_REDIRECTS}"


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema("Search query"),
        count=IntegerSchema(1, description="Results (1-10)", minimum=1, maximum=10),
        category=StringSchema(
            "Search category: web (default), news, academic, github, or all",
        ),
        mode=StringSchema(
            "Search mode: fast (free scrapers) or quality (paid API engines)",
        ),
        required=["query"],
    )
)
class WebSearchTool(Tool):
    _scopes = {"core", "subagent"}
    name = "web_search"
    description = (
        "Search the web across multiple engines concurrently. "
        "Returns titles, URLs, and snippets. "
        "Use category to target news, academic, github, or all engines. "
        "Use mode to select search strategy: fast (free, low latency) or quality (paid, high accuracy). "
        "Use web_fetch to read a specific page in full."
    )
    config_key = "web"

    @classmethod
    def config_cls(cls):
        return WebToolsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.web.enable and ctx.config.web.search.enable

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            max_results=ctx.config.web.search.max_results,
            engines=ctx.config.web.search.engines,
            engine_timeout=ctx.config.web.search.engine_timeout,
            total_timeout=ctx.config.web.search.total_timeout,
            api_keys=ctx.config.web.search.api_keys,
            proxy=ctx.config.web.proxy,
            default_mode=ctx.config.web.search.mode,
        )

    def __init__(
        self,
        max_results: int = 5,
        engines: list[str] | None = None,
        engine_timeout: float = 2.0,
        total_timeout: float = 5.0,
        api_keys: dict[str, list[str]] | None = None,
        proxy: str | None = None,
        default_mode: str = "fast",
    ):
        self.max_results = max_results
        self.engines = engines
        self.engine_timeout = engine_timeout
        self.total_timeout = total_timeout
        self.api_keys = api_keys or {}
        self.proxy = proxy
        self.default_mode = default_mode

    @property
    def read_only(self) -> bool:
        return True

    @property
    def exclusive(self) -> bool:
        return False

    async def execute(
        self,
        query: str,
        count: int | None = None,
        category: str | None = None,
        mode: str | None = None,
        **kwargs: Any,
    ) -> str:
        from openbot.agent.tools.web_search_concurrent import (
            concurrent_search,
            format_concurrent_results,
        )
        n = min(max(count or self.max_results, 1), 10)
        search_mode = kwargs.pop("mode", mode) or self.default_mode
        effective_engines = self.engines
        if effective_engines is None and search_mode in ("fast", "quality"):
            api_engines = {"tavily", "baidu_web_search", "baidu_ai_search"}
            if search_mode == "quality":
                effective_engines = [e for e in api_engines]
            else:
                effective_engines = ["bing", "sogou", "baidu", "360", "duckduckgo", "brave"]
        items, stats = await concurrent_search(
            query=query,
            region=category or "local",
            max_results=n,
            engine_timeout=self.engine_timeout,
            total_timeout=self.total_timeout,
            proxy=self.proxy,
            engines=effective_engines,
            api_keys=self.api_keys or None,
        )
        return format_concurrent_results(query, items, stats, max_display=n)


@tool_parameters(
    tool_parameters_schema(
        url=StringSchema("URL to fetch"),
        extractMode={
            "type": "string",
            "enum": ["markdown", "text"],
            "default": "markdown",
        },
        maxChars=IntegerSchema(0, minimum=100),
        required=["url"],
    )
)
class WebFetchTool(Tool):
    _scopes = {"core", "subagent"}
    name = "web_fetch"
    description = (
        "Fetch a URL and extract readable content (HTML to markdown/text). "
        "Output is capped at maxChars (default 50 000). "
        "Works for most web pages and docs; may fail on login-walled or JS-heavy sites."
    )
    config_key = "web"

    @classmethod
    def config_cls(cls):
        return WebToolsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.web.enable

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            proxy=ctx.config.web.proxy,
            user_agent=ctx.config.web.user_agent,
            config=ctx.config.web.fetch,
        )

    def __init__(
        self,
        proxy: str | None = None,
        user_agent: str | None = None,
        max_chars: int = 50000,
        config: WebFetchConfig | None = None,
    ):
        self.proxy = proxy
        self.user_agent = user_agent or _DEFAULT_USER_AGENT
        self.max_chars = max_chars
        self.config = config or WebFetchConfig()
        self._url_cache: cachetools.TTLCache = cachetools.TTLCache(
            maxsize=100, ttl=self.config.cache_ttl
        )
        self._client: primp.AsyncClient | None = None

    @property
    def read_only(self) -> bool:
        return True

    @property
    def exclusive(self) -> bool:
        return False

    async def _ensure_client(self) -> primp.AsyncClient:
        if self._client is None:
            cfg = self.config
            total_timeout = cfg.connect_timeout + cfg.read_timeout
            self._client = primp.AsyncClient(
                proxy=self.proxy,
                timeout=total_timeout,
            )
        return self._client

    @property
    def _semaphore(self) -> asyncio.Semaphore:
        if not hasattr(self, "__sem"):
            self.__sem = asyncio.Semaphore(self.config.max_concurrency)
        return self.__sem

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def execute(
        self,
        url: str,
        extract_mode: str = "markdown",
        max_chars: int | None = None,
        **kwargs: Any,
    ) -> Any:
        url = url.strip(" \t\r\n`\"'")
        extract_mode = kwargs.pop("extractMode", extract_mode)
        max_chars = kwargs.pop("maxChars", max_chars) or self.max_chars

        is_valid, error_msg = _validate_url_safe(url)
        if not is_valid:
            return json.dumps({"error": f"URL validation failed: {error_msg}", "url": url}, ensure_ascii=False)

        cached = self._url_cache.get(url)
        if cached is not None:
            cached_text, cached_len = cached
            text_with_banner = (
                f"{UNTRUSTED_CONTENT_BANNER}\n\n{cached_text[:max_chars]}"
                if cached_len > max_chars else f"{UNTRUSTED_CONTENT_BANNER}\n\n{cached_text}"
            )
            return json.dumps({
                "url": url, "finalUrl": url, "status": 200,
                "extractor": "cache", "truncated": cached_len > max_chars,
                "length": cached_len, "untrusted": True, "text": text_with_banner,
            }, ensure_ascii=False)

        async with self._semaphore:
            client = await self._ensure_client()
            headers = {"User-Agent": self.user_agent}
            r, redirect_error = await _fetch_with_safe_redirects(client, url, headers=headers)
            if redirect_error:
                return json.dumps({"error": redirect_error, "url": url}, ensure_ascii=False)
            if r is None:
                return json.dumps({"error": "Fetch failed", "url": url}, ensure_ascii=False)

            try:
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if ctype.startswith("image/"):
                    raw = await r.aread()
                    return build_image_content_blocks(raw, ctype, url, f"(Image fetched from: {url})")
                if "application/json" in ctype:
                    text, extractor = json.dumps(r.json(), indent=2, ensure_ascii=False), "json"
                elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                    try:
                        text = await asyncio.wait_for(
                            self._extract_readable_html_async(r.text, extract_mode),
                            timeout=self.config.read_timeout,
                        )
                        extractor = "readability"
                    except asyncio.TimeoutError:
                        logger.warning("Readability timed out for {}, using raw HTML fallback", url)
                        text, extractor = _normalize(_strip_tags(r.text)), "html"
                    except Exception as e:
                        logger.warning("Readability failed for {}, using raw HTML fallback: {}", url, e)
                        text, extractor = _normalize(_strip_tags(r.text)), "html"
                else:
                    text, extractor = r.text, "raw"

                truncated = len(text) > max_chars
                if truncated:
                    text = text[:max_chars]
                self._url_cache[url] = (text, len(text))
                text_with_banner = f"{UNTRUSTED_CONTENT_BANNER}\n\n{text}"
                return json.dumps({
                    "url": url, "finalUrl": str(r.url), "status": r.status_code,
                    "extractor": extractor, "truncated": truncated,
                    "length": len(text), "untrusted": True, "text": text_with_banner,
                }, ensure_ascii=False)
            except primp.ConnectError as e:
                logger.exception("WebFetch proxy error for {}", url)
                return json.dumps({"error": f"Proxy error: {e}", "url": url}, ensure_ascii=False)
            except Exception as e:
                logger.exception("WebFetch error for {}", url)
                return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)
            finally:
                await r.aclose()

    def _extract_readable_html(self, html_content: str, extract_mode: str) -> str:
        from readability import Document
        doc = Document(html_content)
        summary = doc.summary()
        content = self._to_markdown(summary) if extract_mode == "markdown" else _strip_tags(summary)
        return f"# {doc.title()}\n\n{content}" if doc.title() else content

    async def _extract_readable_html_async(self, html_content: str, extract_mode: str) -> str:
        return await asyncio.to_thread(self._extract_readable_html, html_content, extract_mode)

    def _to_markdown(self, html_content: str) -> str:
        text = re.sub(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
            lambda m: f'[{_strip_tags(m[2])}]({m[1]})', html_content, flags=re.I,
        )
        text = re.sub(
            r'<h([1-6])[^>]*>([\s\S]*?)</\1>',
            lambda m: f'\n{"#" * int(m[1])} {_strip_tags(m[2])}\n', text, flags=re.I,
        )
        text = re.sub(r'<li[^>]*>([\s\S]*?)</li>', lambda m: f'\n- {_strip_tags(m[1])}', text, flags=re.I)
        text = re.sub(r'</(p|div|section|article)>', '\n\n', text, flags=re.I)
        text = re.sub(r'<(br|hr)\s*/?>', '\n', text, flags=re.I)
        return _normalize(_strip_tags(text))
