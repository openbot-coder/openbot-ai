"""Local web search engines — adapted from web-search-skills.

Provides:
  - SearchResult: unified result dataclass
  - BaseEngine: abstract base for all engines
  - Web scrapers: BingScraper, BingGlobalScraper, SogouScraper, BaiduScraper,
    Search360Scraper, DuckDuckGoParser, BraveParser, GoogleScraper
  - News: NewsSearch (BingNews + RSS feeds)
  - Academic: AcademicSearch (ArXiv + CrossRef)
  - Code: GitHubEngine
  - WeChat: WeChatSearch (Sogou WeChat public account articles)
  - Hotlist: HotlistEngine (Toutiao/Weibo/Baidu/Bilibili/V2EX/Juejin trending)
  - RSS: RssEngine (standalone RSS/Atom feed engine)
"""

from openbot.agent.tools.web_engines.academic_engine import AcademicSearch
from openbot.agent.tools.web_engines.baidu_scraper import BaiduScraper
from openbot.agent.tools.web_engines.base import BaseEngine, SearchResult
from openbot.agent.tools.web_engines.bing_global_scraper import BingGlobalScraper
from openbot.agent.tools.web_engines.bing_scraper import BingScraper
from openbot.agent.tools.web_engines.github_engine import GitHubEngine
from openbot.agent.tools.web_engines.google_scraper import GoogleScraper
from openbot.agent.tools.web_engines.hotlist_engine import HotlistEngine
from openbot.agent.tools.web_engines.news_engine import NewsSearch
from openbot.agent.tools.web_engines.parser_engines import BraveParser, DuckDuckGoParser
from openbot.agent.tools.web_engines.rss_engine import RssEngine
from openbot.agent.tools.web_engines.search360_scraper import Search360Scraper
from openbot.agent.tools.web_engines.sogou_scraper import SogouScraper
from openbot.agent.tools.web_engines.wechat_engine import WeChatSearch

__all__ = [
    "BaseEngine",
    "SearchResult",
    "BingScraper",
    "BingGlobalScraper",
    "GoogleScraper",
    "SogouScraper",
    "BaiduScraper",
    "Search360Scraper",
    "DuckDuckGoParser",
    "BraveParser",
    "NewsSearch",
    "AcademicSearch",
    "GitHubEngine",
    "WeChatSearch",
    "HotlistEngine",
    "RssEngine",
]
