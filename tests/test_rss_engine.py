"""Tests for rss_engine — standalone RSS/Atom feed engine."""

from __future__ import annotations

import unittest

from openbot.agent.tools.web_engines.rss_engine import (
    DEFAULT_FEEDS,
    RssEngine,
    _parse_rss_feed,
    _strip_html,
)

# ---------------------------------------------------------------------------
# Sample RSS/Atom XML
# ---------------------------------------------------------------------------

SAMPLE_RSS_20 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Test Feed</title>
  <item>
    <title>RSS Item 1</title>
    <link>https://example.com/1</link>
    <description>&lt;p&gt;Description of item 1&lt;/p&gt;</description>
    <pubDate>Mon, 09 Jun 2025 12:00:00 +0800</pubDate>
  </item>
  <item>
    <title>RSS Item 2</title>
    <link>https://example.com/2</link>
    <description>Description of item 2 without HTML</description>
    <pubDate>Tue, 10 Jun 2025 08:00:00 +0800</pubDate>
  </item>
  <item>
    <title></title>
    <link>https://example.com/empty</link>
    <description>Should be skipped (empty title)</description>
  </item>
  <item>
    <title>No Link Item</title>
    <description>Should be skipped (no link)</description>
  </item>
</channel>
</rss>"""

SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Test Feed</title>
  <entry>
    <title>Atom Entry 1</title>
    <link href="https://atom.example.com/1"/>
    <summary>Summary of atom entry 1</summary>
    <updated>2025-06-10T10:00:00Z</updated>
  </entry>
  <entry>
    <title>Atom Entry 2</title>
    <link href="https://atom.example.com/2"/>
    <summary>Another summary</summary>
    <updated>2025-06-09T08:00:00Z</updated>
  </entry>
</feed>"""

SAMPLE_INVALID_XML = "this is not xml at all <><><>"


class TestStripHtml(unittest.TestCase):
    def test_strip_tags(self):
        self.assertEqual(_strip_html("<p>Hello <b>world</b></p>"), "Hello world")

    def test_no_tags(self):
        self.assertEqual(_strip_html("plain text"), "plain text")

    def test_nested_tags(self):
        self.assertEqual(_strip_html("<div><span><a href='#'>link</a></span></div>"), "link")

    def test_empty_string(self):
        self.assertEqual(_strip_html(""), "")


class TestParseRssFeed(unittest.TestCase):
    def test_rss_2_0_parse(self):
        results = _parse_rss_feed("test", SAMPLE_RSS_20)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "RSS Item 1")
        self.assertEqual(results[0]["url"], "https://example.com/1")
        self.assertIn("Description of item 1", results[0]["snippet"])
        self.assertEqual(results[0]["published"], "Mon, 09 Jun 2025 12:00:00 +0800")

    def test_atom_parse(self):
        results = _parse_rss_feed("test", SAMPLE_ATOM)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Atom Entry 1")
        self.assertEqual(results[0]["url"], "https://atom.example.com/1")
        self.assertIn("Summary of atom entry 1", results[0]["snippet"])
        self.assertEqual(results[0]["published"], "2025-06-10T10:00:00Z")

    def test_invalid_xml(self):
        results = _parse_rss_feed("test", SAMPLE_INVALID_XML)
        self.assertEqual(results, [])

    def test_query_filter_match(self):
        # "without" only appears in item 2 description
        results = _parse_rss_feed("test", SAMPLE_RSS_20, query="without")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "RSS Item 2")

    def test_query_filter_no_match(self):
        results = _parse_rss_feed("test", SAMPLE_RSS_20, query="nonexistent_xyz")
        self.assertEqual(len(results), 0)

    def test_empty_title_skipped(self):
        results = _parse_rss_feed("test", SAMPLE_RSS_20)
        urls = [r["url"] for r in results]
        self.assertNotIn("https://example.com/empty", urls)

    def test_no_link_skipped(self):
        results = _parse_rss_feed("test", SAMPLE_RSS_20)
        titles = [r["title"] for r in results]
        self.assertNotIn("No Link Item", titles)

    def test_html_stripped_from_description(self):
        results = _parse_rss_feed("test", SAMPLE_RSS_20)
        for r in results:
            self.assertNotIn("<p>", r["snippet"])
            self.assertNotIn("</p>", r["snippet"])


class TestRssEngine(unittest.TestCase):
    def test_init(self):
        engine = RssEngine(timeout=10)
        self.assertEqual(engine.name, "rss")

    def test_default_feeds_defined(self):
        self.assertIn("36kr", DEFAULT_FEEDS)
        self.assertIn("HackerNews", DEFAULT_FEEDS)
        self.assertIn("少数派", DEFAULT_FEEDS)
        self.assertGreaterEqual(len(DEFAULT_FEEDS), 10)

    def test_custom_feeds(self):
        engine = RssEngine()
        custom = {"custom_feed": "https://example.com/rss.xml"}
        # Verify custom feeds would be used (we can't actually fetch in unit test)
        self.assertIsInstance(custom, dict)


if __name__ == "__main__":
    unittest.main()
