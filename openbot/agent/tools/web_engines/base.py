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

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

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
