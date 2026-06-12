#!/usr/bin/env python3
"""Test concurrent_search orchestrator + web_fetch."""
import asyncio, sys, time, json

sys.path.insert(0, "/home/openbot/workspace/projects/openbot-ai")
if sys.version_info < (3, 11):
    import tomli as tomllib
    sys.modules["tomllib"] = tomllib

from openbot.agent.tools.web_search_concurrent import concurrent_search, format_concurrent_results

async def test_concurrent():
    print("=" * 60)
    print("TEST 1: concurrent_search (default web engines)")
    print("=" * 60)
    t0 = time.monotonic()
    items, stats = await concurrent_search(
        query="latest AI news 2026",
        category="web",
        max_results=5,
        engine_timeout=3.0,
        total_timeout=8.0,
    )
    elapsed = (time.monotonic() - t0) * 1000
    print(format_concurrent_results("latest AI news 2026", items, stats, max_display=5))
    print(f"\n⏱ Total: {elapsed:.0f}ms")

    print("\n" + "=" * 60)
    print("TEST 2: concurrent_search (news category)")
    print("=" * 60)
    t0 = time.monotonic()
    items2, stats2 = await concurrent_search(
        query="stock market",
        category="news",
        max_results=3,
        engine_timeout=3.0,
        total_timeout=8.0,
    )
    elapsed = (time.monotonic() - t0) * 1000
    print(format_concurrent_results("stock market", items2, stats2, max_display=3))
    print(f"\n⏱ Total: {elapsed:.0f}ms")

    print("\n" + "=" * 60)
    print("TEST 3: concurrent_search (github category)")
    print("=" * 60)
    t0 = time.monotonic()
    items3, stats3 = await concurrent_search(
        query="LLM agent framework",
        category="github",
        max_results=3,
        engine_timeout=3.0,
        total_timeout=8.0,
    )
    elapsed = (time.monotonic() - t0) * 1000
    print(format_concurrent_results("LLM agent framework", items3, stats3, max_display=3))
    print(f"\n⏱ Total: {elapsed:.0f}ms")

if __name__ == "__main__":
    asyncio.run(test_concurrent())
