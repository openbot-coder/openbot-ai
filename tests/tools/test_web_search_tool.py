"""Tests for concurrent multi-engine web search."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from openbot.agent.tools.web import WebSearchConfig, WebSearchTool
from openbot.agent.tools.web_engines.api_engine import BaseApiEngine, _QuotaTracker
from openbot.agent.tools.web_engines.base import SearchResult
from openbot.agent.tools.web_search_concurrent import (
    ENGINE_GROUPS,
    SearchStats,
    _deduplicate,
    _normalize_url,
    _ssrf_check,
    concurrent_search,
    format_concurrent_results,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(title="Test", url="https://example.com", snippet="desc",
                 source="test", category="web") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet,
                        source=source, category=category)


def _tool(engines=None, max_results=5, engine_timeout=2.0, total_timeout=5.0) -> WebSearchTool:
    return WebSearchTool(max_results=max_results, engines=engines,
                         engine_timeout=engine_timeout, total_timeout=total_timeout)


# ---------------------------------------------------------------------------
# WebSearchTool construction & properties
# ---------------------------------------------------------------------------

def test_tool_defaults():
    tool = _tool()
    assert tool.max_results == 5
    assert tool.engines is None or isinstance(tool.engines, list)
    assert tool.read_only is True
    assert tool.exclusive is False
    assert tool.concurrency_safe is True
    assert tool.engine_timeout == 2.0
    assert tool.total_timeout == 5.0


def test_tool_custom_engines():
    tool = _tool(engines=["bing", "duckduckgo"])
    assert tool.engines == ["bing", "duckduckgo"]


def test_tool_custom_timeout():
    tool = _tool(engine_timeout=3.0, total_timeout=10.0)
    assert tool.engine_timeout == 3.0
    assert tool.total_timeout == 10.0


def test_web_search_config_defaults():
    cfg = WebSearchConfig()
    assert cfg.max_results == 5
    assert "bing" in cfg.engines
    assert "duckduckgo" in cfg.engines
    assert cfg.engine_timeout == 2.0
    assert cfg.total_timeout == 5.0


def test_web_search_config_custom():
    cfg = WebSearchConfig(max_results=10, engines=["bing"], engine_timeout=3.0, total_timeout=10.0)
    assert cfg.max_results == 10
    assert cfg.engines == ["bing"]
    assert cfg.engine_timeout == 3.0
    assert cfg.total_timeout == 10.0


# ---------------------------------------------------------------------------
# Engine groups
# ---------------------------------------------------------------------------

def test_engine_groups_defined():
    assert "local" in ENGINE_GROUPS
    assert "global" in ENGINE_GROUPS
    assert "news" in ENGINE_GROUPS
    assert "academic" in ENGINE_GROUPS
    assert "github" in ENGINE_GROUPS
    assert "hotlist" in ENGINE_GROUPS
    assert "rss" in ENGINE_GROUPS
    assert "all" in ENGINE_GROUPS
    assert len(ENGINE_GROUPS["local"]) >= 4
    assert len(ENGINE_GROUPS["all"]) > len(ENGINE_GROUPS["local"])
    assert "hotlist" in ENGINE_GROUPS["all"]
    assert "rss" in ENGINE_GROUPS["all"]


# ---------------------------------------------------------------------------
# URL normalization & deduplication
# ---------------------------------------------------------------------------

def test_normalize_url():
    assert _normalize_url("https://Example.com/path/") == "https://example.com/path"
    assert _normalize_url("  https://test.com  ") == "https://test.com"


def test_deduplicate_removes_duplicates():
    items = [
        {"title": "Alpha", "url": "https://a.com"},
        {"title": "Alpha2", "url": "https://a.com/"},  # same as first after normalize
        {"title": "Beta", "url": "https://b.com"},
    ]
    result = _deduplicate(items)
    assert len(result) == 2
    # First occurrence wins
    assert result[0]["title"] == "Alpha"
    assert result[1]["url"] == "https://b.com"


def test_deduplicate_filters_short_titles():
    items = [
        {"title": "OK", "url": "https://ok.com"},
        {"title": "X", "url": "https://short.com"},  # title too short
        {"title": "", "url": "https://empty.com"},
    ]
    result = _deduplicate(items)
    assert len(result) == 1
    assert result[0]["url"] == "https://ok.com"


# ---------------------------------------------------------------------------
# SSRF check
# ---------------------------------------------------------------------------

def test_ssrf_check_unknown_engine():
    ok, err = _ssrf_check("nonexistent_engine", "test")
    assert ok is True  # unknown engines pass through


def test_ssrf_check_known_engine(monkeypatch):
    # Mock validate_url_target to always pass
    monkeypatch.setattr(
        "openbot.security.network.validate_url_target",
        lambda url: (True, ""),
    )
    ok, err = _ssrf_check("bing", "test query")
    assert ok is True


def test_ssrf_check_blocks_private_ip(monkeypatch):
    monkeypatch.setattr(
        "openbot.security.network.validate_url_target",
        lambda url: (False, "resolved to private IP"),
    )
    ok, err = _ssrf_check("bing", "test query")
    assert ok is False
    assert "private IP" in err


# ---------------------------------------------------------------------------
# Concurrent search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_search_basic():
    """Test basic concurrent search with mocked engines."""
    mock_results = [_make_result(title="Result 1", url="https://r1.com", source="bing")]

    with patch(
        "openbot.agent.tools.web_search_concurrent._build_engine_instances",
        return_value={
            "bing": _MockEngine(mock_results),
        },
    ), patch(
        "openbot.agent.tools.web_search_concurrent._ssrf_check",
        return_value=(True, ""),
    ):
        items, stats = await concurrent_search(
            query="test",
            engines=["bing"],
            max_results=5,
        )

    assert len(items) >= 1
    assert items[0]["title"] == "Result 1"
    assert stats.succeeded == 1
    assert stats.total_engines == 1


@pytest.mark.asyncio
async def test_concurrent_search_engine_timeout():
    """Test that slow engines are timed out individually."""
    slow_engine = _SlowEngine(delay=10.0)

    with patch(
        "openbot.agent.tools.web_search_concurrent._build_engine_instances",
        return_value={"bing": slow_engine},
    ), patch(
        "openbot.agent.tools.web_search_concurrent._ssrf_check",
        return_value=(True, ""),
    ):
        items, stats = await concurrent_search(
            query="test",
            engines=["bing"],
            engine_timeout=0.1,
            total_timeout=5.0,
        )

    assert len(items) == 0
    assert "bing" in stats.timed_out


@pytest.mark.asyncio
async def test_concurrent_search_total_timeout():
    """Test that total timeout truncates long-running searches."""
    slow1 = _SlowEngine(delay=10.0)
    slow2 = _SlowEngine(delay=10.0)

    with patch(
        "openbot.agent.tools.web_search_concurrent._build_engine_instances",
        return_value={"bing": slow1, "sogou": slow2},
    ), patch(
        "openbot.agent.tools.web_search_concurrent._ssrf_check",
        return_value=(True, ""),
    ):
        items, stats = await concurrent_search(
            query="test",
            engines=["bing", "sogou"],
            engine_timeout=10.0,
            total_timeout=0.2,
        )

    # Both should be timed out or failed
    assert stats.succeeded == 0


@pytest.mark.asyncio
async def test_concurrent_search_mixed_results():
    """Test merging results from multiple engines."""
    bing_results = [
        _make_result("Bing 1", "https://b1.com", source="bing"),
        _make_result("Shared", "https://shared.com", source="bing"),
    ]
    sogou_results = [
        _make_result("Sogou 1", "https://s1.com", source="sogou"),
        _make_result("Shared", "https://shared.com", source="sogou"),  # duplicate
    ]

    with patch(
        "openbot.agent.tools.web_search_concurrent._build_engine_instances",
        return_value={
            "bing": _MockEngine(bing_results),
            "sogou": _MockEngine(sogou_results),
        },
    ), patch(
        "openbot.agent.tools.web_search_concurrent._ssrf_check",
        return_value=(True, ""),
    ):
        items, stats = await concurrent_search(
            query="test",
            engines=["bing", "sogou"],
            max_results=10,
        )

    assert stats.succeeded == 2
    # Shared URL should be deduplicated
    urls = [item["url"] for item in items]
    assert urls.count("https://shared.com") == 1
    assert stats.total_raw == 4
    assert stats.deduplicated == 3


@pytest.mark.asyncio
async def test_concurrent_search_ssrf_blocked():
    """Test that SSRF-blocked engines are skipped."""
    with patch(
        "openbot.agent.tools.web_search_concurrent._build_engine_instances",
        return_value={"bing": _MockEngine([_make_result()])},
    ), patch(
        "openbot.agent.tools.web_search_concurrent._ssrf_check",
        return_value=(False, "private IP"),
    ):
        items, stats = await concurrent_search(
            query="test",
            engines=["bing"],
        )

    assert len(items) == 0
    assert "bing" in stats.failed


@pytest.mark.asyncio
async def test_concurrent_search_category_routing():
    """Test that category selects the right engine group."""
    with patch(
        "openbot.agent.tools.web_search_concurrent._build_engine_instances",
        return_value={
            "news": _MockEngine([_make_result("News", "https://news.com", category="news")]),
        },
    ), patch(
        "openbot.agent.tools.web_search_concurrent._ssrf_check",
        return_value=(True, ""),
    ):
        items, stats = await concurrent_search(
            query="AI",
            region="news",
        )

    assert stats.total_engines == 1
    assert items[0]["category"] == "news"


# ---------------------------------------------------------------------------
# WebSearchTool.execute()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_default():
    tool = _tool(engines=["bing"])

    with patch(
        "openbot.agent.tools.web_search_concurrent.concurrent_search",
        new_callable=AsyncMock,
        return_value=(
            [{"title": "Test", "url": "https://test.com", "snippet": "snippet",
              "source": "bing", "category": "web", "rank": 1}],
            SearchStats(total_engines=1, succeeded=1, duration_ms=100),
        ),
    ) as mock_search:
        result = await tool.execute(query="openbot")
        assert "Test" in result
        assert "https://test.com" in result
        mock_search.assert_called_once()


@pytest.mark.asyncio
async def test_execute_passes_timeout_to_concurrent_search():
    tool = _tool(engines=["bing"], engine_timeout=3.0, total_timeout=10.0)

    with patch(
        "openbot.agent.tools.web_search_concurrent.concurrent_search",
        new_callable=AsyncMock,
        return_value=(
            [],
            SearchStats(total_engines=1, succeeded=0, duration_ms=100),
        ),
    ) as mock_search:
        await tool.execute(query="test")
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["engine_timeout"] == 3.0
        assert call_kwargs["total_timeout"] == 10.0


@pytest.mark.asyncio
async def test_execute_with_category():
    tool = _tool()

    with patch(
        "openbot.agent.tools.web_search_concurrent.concurrent_search",
        new_callable=AsyncMock,
        return_value=(
            [_make_result("Paper", "https://arxiv.org/123").to_dict()],
            SearchStats(total_engines=1, succeeded=1, duration_ms=200),
        ),
    ) as mock_search:
        await tool.execute(query="transformer", category="academic")
        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["region"] == "academic"


# ---------------------------------------------------------------------------
# Format output
# ---------------------------------------------------------------------------

def test_format_concurrent_results_with_banner():
    items = [
        {"title": "Result", "url": "https://r.com", "snippet": "desc",
         "source": "bing", "rank": 1},
    ]
    stats = SearchStats(total_engines=2, succeeded=1, failed=["sogou"], duration_ms=1500)
    text = format_concurrent_results("test", items, stats)

    assert "[External content" in text
    assert "Result" in text
    assert "https://r.com" in text
    assert "1/2 ok" in text
    assert "1500ms" in text
    assert "sogou" in text


def test_format_concurrent_results_no_results():
    stats = SearchStats(total_engines=3, succeeded=0, duration_ms=500)
    text = format_concurrent_results("query", [], stats)
    assert "No results" in text
    assert "[External content" in text


# ---------------------------------------------------------------------------
# Mock engines
# ---------------------------------------------------------------------------

class _MockEngine:
    """Engine that returns pre-configured results."""
    name = "mock"
    search_type = "search"

    def __init__(self, results=None):
        self._results = results or []

    async def search(self, query, max_results=10, **kwargs):
        return self._results[:max_results]


class _SlowEngine:
    """Engine that sleeps for a configurable delay."""
    name = "slow"
    search_type = "search"

    def __init__(self, delay=5.0):
        self.delay = delay

    async def search(self, query, max_results=10, **kwargs):
        await asyncio.sleep(self.delay)
        return []


# ---------------------------------------------------------------------------
# _QuotaTracker tests
# ---------------------------------------------------------------------------

class TestQuotaTracker:
    """Unit tests for _QuotaTracker key rotation and cooldown logic."""

    def test_pick_key_returns_least_used(self):
        tracker = _QuotaTracker(max_queries_per_key=10, cooldown_seconds=60)
        tracker.record_success("key_A")
        tracker.record_success("key_A")
        tracker.record_success("key_B")
        key = tracker.pick_key(["key_A", "key_B", "key_C"])
        assert key == "key_C"  # zero usage preferred

    def test_pick_key_none_when_all_in_cooldown(self):
        tracker = _QuotaTracker(max_queries_per_key=10, cooldown_seconds=9999)
        for _ in range(3):
            tracker.record_failure("key_A")
        assert tracker.pick_key(["key_A"]) is None

    def test_cooldown_expires_allows_retry(self):
        tracker = _QuotaTracker(max_queries_per_key=10, cooldown_seconds=0)
        tracker.record_failure("key_A")
        tracker.record_failure("key_A")
        assert tracker.pick_key(["key_A"]) is not None  # cooldown 0 → immediately available

    def test_record_success_resets_failures(self):
        tracker = _QuotaTracker(max_queries_per_key=10, cooldown_seconds=60)
        tracker.record_failure("key_A")
        tracker.record_failure("key_A")
        tracker.record_success("key_A")
        assert tracker.pick_key(["key_A"]) == "key_A"

    def test_reset_daily_clears_quota(self):
        tracker = _QuotaTracker(max_queries_per_key=10, cooldown_seconds=0)
        tracker.record_success("key_A")
        tracker.record_success("key_A")
        assert tracker._states["key_A"].quota_used == 2
        tracker.reset_daily()
        assert tracker._states["key_A"].quota_used == 0


class TestBaseApiEngineKeyManagement:
    """Tests for BaseApiEngine key configuration and quota integration."""

    def test_configure_keys_strips_whitespace(self):
        eng = _FakeApiEngine(timeout=5.0)
        eng.configure_keys(["  key1  ", "key2"])
        assert eng._keys == ["key1", "key2"]

    def test_configure_keys_rejects_empty(self):
        eng = _FakeApiEngine(timeout=5.0)
        with pytest.raises(ValueError, match="At least one API key"):
            eng.configure_keys([])

    def test_no_keys_returns_empty(self):
        eng = _FakeApiEngine(timeout=5.0)
        assert eng._keys == []

    @pytest.mark.asyncio
    async def test_quota_exhaustion_blocks_key(self):
        eng = _FakeApiEngine(timeout=5.0)
        eng.configure_keys(["k1"], max_queries_per_key=2)
        await eng.search("test")
        await eng.search("test")
        # 2 used, max is 2 → next should get empty (quota exhausted)
        results = await eng.search("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_cooldown_after_three_failures(self):
        eng = _FakeApiEngine(timeout=0.01)
        eng.configure_keys(["k1"], max_queries_per_key=100)

        async def _fail(*a, **kw):
            raise TimeoutError("boom")

        eng._search_with_key = _fail  # type: ignore[method-assign]

        for _ in range(3):
            await eng.search("test")

        # After 3 failures, key is in cooldown — search returns empty without calling engine
        call_count = 0

        async def _counting_fail(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise TimeoutError("boom")

        eng._search_with_key = _counting_fail  # type: ignore[method-assign]
        await eng.search("test")
        assert call_count == 0  # skipped due to cooldown


# ---------------------------------------------------------------------------
# API engine groups
# ---------------------------------------------------------------------------

def test_api_engine_groups_registered():
    assert "baidu_web_search" in ENGINE_GROUPS
    assert "baidu_ai_search" in ENGINE_GROUPS
    assert "tavily" in ENGINE_GROUPS
    assert "api" in ENGINE_GROUPS
    assert "baidu_web_search" in ENGINE_GROUPS["api"]
    assert "baidu_ai_search" in ENGINE_GROUPS["api"]
    assert "tavily" in ENGINE_GROUPS["api"]


# ---------------------------------------------------------------------------
# WebSearchConfig api_keys
# ---------------------------------------------------------------------------

def test_web_search_config_api_keys_default_empty():
    cfg = WebSearchConfig()
    assert cfg.api_keys == {}


def test_web_search_config_api_keys_custom():
    cfg = WebSearchConfig(api_keys={"baidu_web_search": ["k1", "k2"], "tavily": ["t1"]})
    assert cfg.api_keys == {"baidu_web_search": ["k1", "k2"], "tavily": ["t1"]}


# ---------------------------------------------------------------------------
# BaseApiEngine abstract interface
# ---------------------------------------------------------------------------

class _FakeApiEngine(BaseApiEngine):
    """Concrete stub for testing BaseApiEngine orchestration."""
    _provider_name = "fake"

    async def _search_with_key(self, query, max_results, api_key):
        return [SearchResult(title="ok", url="https://x.com", source="fake")]


@pytest.mark.asyncio
async def test_base_api_engine_no_keys_returns_empty():
    eng = _FakeApiEngine(timeout=5.0)
    results = await eng.search("test")
    assert results == []


@pytest.mark.asyncio
async def test_base_api_engine_key_rotation_on_timeout():
    eng = _FakeApiEngine(timeout=0.01)
    eng.configure_keys(["k1", "k2"], max_queries_per_key=100)

    async def _slow(*a, **kw):
        await asyncio.sleep(0.1)
        return []

    eng._search_with_key = _slow  # type: ignore[method-assign]
    results = await eng.search("test")
    assert results == []


@pytest.mark.asyncio
async def test_base_api_engine_records_quota_on_success():
    eng = _FakeApiEngine(timeout=5.0)
    eng.configure_keys(["k1"], max_queries_per_key=100)
    results = await eng.search("test")
    assert len(results) == 1
    # key should be usable again
    results2 = await eng.search("test")
    assert len(results2) == 1


@pytest.mark.asyncio
async def test_base_api_engine_cooldown_after_failures():
    eng = _FakeApiEngine(timeout=0.01)
    eng.configure_keys(["k1"], max_queries_per_key=100)

    async def _fail(*a, **kw):
        raise TimeoutError("boom")

    eng._search_with_key = _fail  # type: ignore[method-assign]

    for _ in range(3):
        await eng.search("test")

    # After 3 failures, key should be in cooldown → returns empty without calling
    call_count = 0
    orig = eng._search_with_key

    async def _counting_fail(*a, **kw):
        nonlocal call_count
        call_count += 1
        raise TimeoutError("boom")

    eng._search_with_key = _counting_fail  # type: ignore[method-assign]
    await eng.search("test")
    assert call_count == 0  # skipped due to cooldown


# ---------------------------------------------------------------------------
# concurrent_search with api_keys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_search_with_api_keys():
    """API engines should be instantiated when api_keys are provided."""

    def _fake_build(timeout=10.0, proxy=None, api_keys=None):
        engines = {"bing": _MockEngine([])}
        if api_keys and "tavily" in api_keys:
            from openbot.agent.tools.web_engines.api_engine import TavilyEngine
            tv = TavilyEngine(timeout=timeout, proxy=proxy)
            tv.configure_keys(api_keys["tavily"])
            engines["tavily"] = tv
        return engines

    with patch(
        "openbot.agent.tools.web_search_concurrent._build_engine_instances",
        side_effect=_fake_build,
    ), patch(
        "openbot.agent.tools.web_search_concurrent._ssrf_check",
        return_value=(True, ""),
    ):
        items, stats = await concurrent_search(
            query="test",
            engines=["tavily"],
            api_keys={"tavily": ["tv-key-1"]},
        )

    assert stats.total_engines >= 1
    assert "tavily" in [e for e in stats.per_engine]


def test_web_search_tool_passes_api_keys():
    tool = WebSearchTool(
        max_results=5,
        api_keys={"baidu_web_search": ["bk1", "bk2"], "tavily": ["tv1"]},
    )
    assert tool.api_keys == {"baidu_web_search": ["bk1", "bk2"], "tavily": ["tv1"]}


@pytest.mark.asyncio
async def test_web_search_tool_execute_forwards_api_keys():
    tool = WebSearchTool(
        max_results=5,
        api_keys={"baidu_web_search": ["bk1"]},
    )

    with patch(
        "openbot.agent.tools.web_search_concurrent.concurrent_search",
        new_callable=AsyncMock,
        return_value=(
            [],
            SearchStats(total_engines=1, succeeded=0, duration_ms=50),
        ),
    ) as mock_search:
        await tool.execute(query="hello")
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["api_keys"] == {"baidu_web_search": ["bk1"]}
