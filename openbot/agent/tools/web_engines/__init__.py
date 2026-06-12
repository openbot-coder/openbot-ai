"""Local web search engines — adapted from web-search-skills.

Provides:
  - SearchResult: unified result dataclass
  - BaseEngine: abstract base for all engines
  - Web scrapers: BingScraper, SogouScraper, BaiduScraper, Search360Scraper,
    DuckDuckGoParser, BraveParser
  - News: NewsSearch (BingNews + RSS feeds)
  - Academic: AcademicSearch (ArXiv + CrossRef)
  - Code: GitHubEngine
"""

from openbot.agent.tools.web_engines.academic_engine import AcademicSearch
from openbot.agent.tools.web_engines.baidu_scraper import BaiduScraper
from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult
from openbot.agent.tools.web_engines.bing_scraper import BingScraper
from openbot.agent.tools.web_engines.github_engine import GitHubEngine
from openbot.agent.tools.web_engines.news_engine import NewsSearch
from openbot.agent.tools.web_engines.parser_engines import BraveParser, DuckDuckGoParser
from openbot.agent.tools.web_engines.search360_scraper import Search360Scraper
from openbot.agent.tools.web_engines.sogou_scraper import SogouScraper

__all__ = [
    "BaseEngine",
    "SearchResult",
    "BingScraper",
    "SogouScraper",
    "BaiduScraper",
    "Search360Scraper",
    "DuckDuckGoParser",
    "BraveParser",
    "NewsSearch",
    "AcademicSearch",
    "GitHubEngine",
]
