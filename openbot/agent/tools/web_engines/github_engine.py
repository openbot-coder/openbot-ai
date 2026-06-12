"""GitHub code search engine — uses GitHub API (no key needed for basic search)."""

from __future__ import annotations

import logging
import time

import httpx

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)

HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "openbot/1.0",
}


class GitHubEngine(BaseEngine):
    """GitHub code & repo search via public API."""

    name = "github"

    async def search(self, query: str, max_results: int = 10,
                     search_type: str = "repositories", **kwargs) -> list[SearchResult]:
        """search_type: 'repositories' | 'code' | 'issues'"""
        t0 = time.time()
        endpoint = f"https://api.github.com/search/{search_type}"
        params = {"q": query, "per_page": min(max_results, 30), "sort": "stars"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(endpoint, headers=HEADERS, params=params)
                resp.raise_for_status()
            data = resp.json()
            elapsed = time.time() - t0

            results = []
            for item in data.get("items", [])[:max_results]:
                if search_type == "repositories":
                    results.append(SearchResult(
                        title=item.get("full_name", ""),
                        url=item.get("html_url", ""),
                        snippet=item.get("description", "") or "",
                        source="github",
                        category="github",
                        extra={
                            "stars": item.get("stargazers_count", 0),
                            "language": item.get("language", ""),
                            "updated": item.get("updated_at", ""),
                        },
                    ))
                elif search_type == "code":
                    results.append(SearchResult(
                        title=item.get("name", ""),
                        url=item.get("html_url", ""),
                        snippet=item.get("text_matches", [{}])[0].get("fragment", "")
                               if item.get("text_matches") else "",
                        source="github",
                        category="github",
                        extra={
                            "repo": item.get("repository", {}).get("full_name", ""),
                            "language": item.get("language", ""),
                        },
                    ))
                elif search_type == "issues":
                    results.append(SearchResult(
                        title=item.get("title", ""),
                        url=item.get("html_url", ""),
                        snippet=item.get("body", "")[:300] if item.get("body") else "",
                        source="github",
                        category="github",
                        extra={
                            "state": item.get("state", ""),
                            "repo": item.get("repository_url", "").split("/")[-2:]
                                   if item.get("repository_url") else [],
                        },
                    ))

            logger.info("[github] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[github] failed: %s", e)
            return []
