"""Academic search — ArXiv + CrossRef APIs (free, no API key)."""

from __future__ import annotations

import logging
import time
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult

logger = logging.getLogger(__name__)


class ArxivEngine(BaseEngine):
    """ArXiv paper search via public API."""

    name = "arxiv"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        t0 = time.time()
        url = (
            f"http://export.arxiv.org/api/query?"
            f"search_query=all:{quote_plus(query)}&start=0&max_results={max_results}"
            f"&sortBy=relevance&sortOrder=descending"
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            elapsed = time.time() - t0
            results = self._parse_xml(resp.text, max_results)
            logger.info("[arxiv] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[arxiv] failed: %s", e)
            return []

    def _parse_xml(self, xml_text: str, max_results: int) -> list[SearchResult]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        results = []

        for entry in root.findall("atom:entry", ns)[:max_results]:
            title = entry.findtext("atom:title", "", ns).strip().replace("\n", " ")
            summary = entry.findtext("atom:summary", "", ns).strip().replace("\n", " ")
            link_el = entry.find("atom:link[@type='text/html']", ns)
            if link_el is None:
                link_el = entry.find("atom:id", ns)
            url = link_el.get("href", "") if link_el is not None else ""
            published = entry.findtext("atom:published", "", ns)
            authors = [a.findtext("atom:name", "", ns) for a in entry.findall("atom:author", ns)]
            categories = [c.get("term", "") for c in entry.findall("atom:category", ns)]

            if title:
                results.append(SearchResult(
                    title=title, url=url, snippet=summary[:300],
                    source="arxiv", category="academic",
                    published=published,
                    extra={"authors": authors[:5], "categories": categories},
                ))

        return results


class CrossRefEngine(BaseEngine):
    """CrossRef academic search via public API."""

    name = "crossref"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        t0 = time.time()
        url = "https://api.crossref.org/works"
        params = {"query": query, "rows": min(max_results, 20),
                  "sort": "relevance", "order": "desc"}
        headers = {"User-Agent": "openbot/1.0 (mailto:search@example.com)"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
            elapsed = time.time() - t0
            data = resp.json()
            results = []
            for item in data.get("message", {}).get("items", [])[:max_results]:
                title_list = item.get("title", [])
                title = title_list[0] if title_list else ""
                doi = item.get("DOI", "")
                url_link = item.get("URL", f"https://doi.org/{doi}")
                abstract = item.get("abstract", "")[:300]
                pub_date = ""
                if item.get("published-print"):
                    dp = item["published-print"].get("date-parts", [[]])[0]
                    pub_date = "-".join(str(p) for p in dp if p) if dp else ""

                authors = [f"{a.get('given', '')} {a.get('family', '')}".strip()
                           for a in item.get("author", [])]

                if title:
                    results.append(SearchResult(
                        title=title, url=url_link, snippet=abstract,
                        source="crossref", category="academic",
                        published=pub_date,
                        extra={
                            "doi": doi,
                            "authors": authors[:5],
                            "journal": (item.get("container-title", [""])[0]
                                        if item.get("container-title") else ""),
                        },
                    ))

            logger.info("[crossref] %d results in %.2fs", len(results), elapsed)
            return results
        except Exception as e:
            logger.warning("[crossref] failed: %s", e)
            return []


class AcademicSearch(BaseEngine):
    """Unified academic search — ArXiv + CrossRef in parallel."""

    name = "academic"

    async def search(self, query: str, max_results: int = 10,
                     **kwargs) -> list[SearchResult]:
        import asyncio
        arxiv = ArxivEngine(timeout=self.timeout, proxy=self.proxy)
        crossref = CrossRefEngine(timeout=self.timeout, proxy=self.proxy)

        arxiv_results, crossref_results = await asyncio.gather(
            arxiv.search(query, max_results=max_results),
            crossref.search(query, max_results=max_results),
            return_exceptions=True,
        )

        all_results = []
        if isinstance(arxiv_results, list):
            all_results.extend(arxiv_results)
        if isinstance(crossref_results, list):
            all_results.extend(crossref_results)

        seen = set()
        deduped = []
        for r in all_results:
            if r.url not in seen:
                seen.add(r.url)
                deduped.append(r)

        return deduped[:max_results]
