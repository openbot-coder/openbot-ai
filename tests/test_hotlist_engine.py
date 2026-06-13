"""Tests for hotlist_engine — all platforms including Bilibili/V2EX/Juejin."""

from __future__ import annotations

import json
import unittest

from openbot.agent.tools.web_engines.hotlist_engine import (
    HotlistEngine,
    _parse_baidu,
    _parse_bilibili,
    _parse_cls,
    _parse_juejin,
    _parse_toutiao,
    _parse_v2ex,
    _parse_weibo,
)

# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

SAMPLE_TOUTIAO = {
    "data": [
        {
            "ClusterIdStr": "12345",
            "Title": "Test Title 1",
            "HotValue": "1000000",
            "LabelDesc": "Hot event",
        },
        {
            "ClusterIdStr": "67890",
            "Title": "Test Title 2",
            "HotValue": "500000",
            "LabelDesc": "",
        },
    ]
}

SAMPLE_WEIBO = {
    "data": {
        "realtime": [
            {"note": "Weibo Topic 1", "num": 2000000, "word": "WeiboTopic1", "category": "entertainment"},
            {"note": "Weibo Topic 2", "num": 1500000, "word": "WeiboTopic2", "category": "society"},
        ]
    }
}

SAMPLE_BAIDU_HTML = """
<html>
<!--s-data:{"data":{"cards":[{"content":[
  {"word":"Baidu Topic 1","url":"https://www.baidu.com/s?wd=t1","hotScore":"3000000","desc":"Description 1"},
  {"word":"Baidu Topic 2","url":"https://www.baidu.com/s?wd=t2","hotScore":"2500000","desc":"Description 2"}
]}]}}-->
</html>
"""

SAMPLE_BILIBILI = {
    "data": {
        "list": [
            {
                "title": "Bilibili Video 1",
                "bvid": "BV1xx411c7mD",
                "desc": "An interesting video",
                "stat": {"view": 1000000, "like": 50000},
                "owner": {"name": "UP主A"},
            },
            {
                "title": "Bilibili Video 2",
                "bvid": "BV1yy522d8eF",
                "desc": "",
                "stat": {"view": 500000, "like": 25000},
                "owner": {"name": "UP主B"},
            },
        ]
    }
}

SAMPLE_V2EX = [
    {
        "title": "V2EX Topic 1",
        "url": "https://www.v2ex.com/t/12345",
        "replies": 42,
        "member": {"username": "v2ex_user"},
    },
    {
        "title": "V2EX Topic 2",
        "url": "https://www.v2ex.com/t/67890",
        "replies": 15,
        "member": {"username": "another_user"},
    },
]

SAMPLE_JUEJIN = {
    "data": [
        {
            "content": {
                "title": "Juejin Article 1",
                "article_id": "7212345678901234",
                "digg_count": 1200,
                "view_count": 50000,
                "author_user_info": {"user_name": "juejin_author"},
            }
        },
        {
            "content": {
                "title": "Juejin Article 2",
                "article_id": "7298765432109876",
                "digg_count": 800,
                "view_count": 30000,
                "author_user_info": {"user_name": "another_author"},
            }
        },
    ]
}

SAMPLE_CLS = {
    "errno": 0,
    "data": {
        "roll_data": [
            {
                "title": "CLS News Title 1",
                "content": "CLS News Title 1 财联社电报内容详情",
                "ctime": 1781327223,
                "level": "A",
                "id": 2399013,
                "reading_num": 127451,
            },
            {
                "title": "",
                "content": "CLS brief content without title",
                "ctime": 1781327100,
                "level": "C",
                "id": 2399012,
                "reading_num": 5000,
            },
            {
                "title": "CLS News Title 3",
                "content": "CLS content 3",
                "ctime": 1781327000,
                "level": "B",
                "id": 2399011,
                "reading_num": 0,
            },
        ]
    },
}


class TestParseToutiao(unittest.TestCase):
    def test_basic_parse(self):
        results = _parse_toutiao(SAMPLE_TOUTIAO)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Test Title 1")
        self.assertEqual(results[0]["hot_value"], 1000000)
        self.assertEqual(results[0]["platform"], "toutiao")
        self.assertIn("12345", results[0]["url"])

    def test_empty_data(self):
        results = _parse_toutiao({"data": []})
        self.assertEqual(results, [])

    def test_missing_title_skipped(self):
        data = {"data": [{"HotValue": "100"}]}
        results = _parse_toutiao(data)
        self.assertEqual(results, [])


class TestParseWeibo(unittest.TestCase):
    def test_basic_parse(self):
        results = _parse_weibo(SAMPLE_WEIBO)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Weibo Topic 1")
        self.assertEqual(results[0]["hot_value"], 2000000)
        self.assertEqual(results[0]["platform"], "weibo")
        self.assertIn("WeiboTopic1", results[0]["url"])

    def test_empty_data(self):
        results = _parse_weibo({"data": {"realtime": []}})
        self.assertEqual(results, [])

    def test_missing_note_skipped(self):
        data = {"data": {"realtime": [{"num": 100}]}}
        results = _parse_weibo(data)
        self.assertEqual(results, [])


class TestParseBaidu(unittest.TestCase):
    def test_basic_parse(self):
        results = _parse_baidu(SAMPLE_BAIDU_HTML)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Baidu Topic 1")
        self.assertEqual(results[0]["hot_value"], 3000000)
        self.assertEqual(results[0]["platform"], "baidu")
        self.assertEqual(results[0]["url"], "https://www.baidu.com/s?wd=t1")

    def test_no_sdata_comment(self):
        results = _parse_baidu("<html><body>no data here</body></html>")
        self.assertEqual(results, [])

    def test_invalid_json(self):
        results = _parse_baidu("<html><!--s-data:not json--></html>")
        self.assertEqual(results, [])


class TestParseBilibili(unittest.TestCase):
    def test_basic_parse(self):
        results = _parse_bilibili(SAMPLE_BILIBILI)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Bilibili Video 1")
        self.assertEqual(results[0]["hot_value"], 1000000)
        self.assertEqual(results[0]["platform"], "bilibili")
        self.assertIn("BV1xx411c7mD", results[0]["url"])
        self.assertIn("UP主A", results[0]["snippet"])

    def test_empty_data(self):
        results = _parse_bilibili({"data": {"list": []}})
        self.assertEqual(results, [])

    def test_missing_title_skipped(self):
        data = {"data": {"list": [{"bvid": "BV123", "stat": {"view": 100, "like": 10}}]}}
        results = _parse_bilibili(data)
        self.assertEqual(results, [])

    def test_no_owner(self):
        data = {"data": {"list": [{"title": "Test", "bvid": "BV123", "stat": {"view": 100, "like": 10}}]}}
        results = _parse_bilibili(data)
        self.assertEqual(len(results), 1)
        self.assertIn("👁100", results[0]["snippet"])


class TestParseV2ex(unittest.TestCase):
    def test_basic_parse(self):
        results = _parse_v2ex(SAMPLE_V2EX)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "V2EX Topic 1")
        self.assertEqual(results[0]["hot_value"], 42)
        self.assertEqual(results[0]["platform"], "v2ex")
        self.assertIn("v2ex_user", results[0]["snippet"])

    def test_empty_data(self):
        results = _parse_v2ex([])
        self.assertEqual(results, [])

    def test_missing_title_skipped(self):
        data = [{"replies": 5}]
        results = _parse_v2ex(data)
        self.assertEqual(results, [])


class TestParseJuejin(unittest.TestCase):
    def test_basic_parse(self):
        results = _parse_juejin(SAMPLE_JUEJIN)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Juejin Article 1")
        self.assertEqual(results[0]["hot_value"], 51200)  # 1200 + 50000
        self.assertEqual(results[0]["platform"], "juejin")
        self.assertIn("7212345678901234", results[0]["url"])
        self.assertIn("juejin_author", results[0]["snippet"])

    def test_empty_data(self):
        results = _parse_juejin({"data": []})
        self.assertEqual(results, [])

    def test_list_input(self):
        results = _parse_juejin(SAMPLE_JUEJIN["data"])
        self.assertEqual(len(results), 2)

    def test_missing_title_skipped(self):
        data = {"data": [{"content": {"article_id": "123"}}]}
        results = _parse_juejin(data)
        self.assertEqual(results, [])


class TestParseCls(unittest.TestCase):
    def test_basic_parse(self):
        results = _parse_cls(SAMPLE_CLS)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["title"], "CLS News Title 1")
        self.assertEqual(results[0]["platform"], "cls")
        self.assertIn("🔴重大", results[0]["snippet"])
        self.assertEqual(results[0]["hot_value"], 127451)
        self.assertIn("2399013", results[0]["url"])

    def test_no_title_uses_content(self):
        results = _parse_cls(SAMPLE_CLS)
        # Second item has no title, should use content prefix
        self.assertEqual(results[1]["title"], "CLS brief content without title")

    def test_level_labels(self):
        results = _parse_cls(SAMPLE_CLS)
        self.assertIn("🔴重大", results[0]["snippet"])   # A
        self.assertIn("⚪次要", results[1]["snippet"])   # C
        self.assertIn("🟡一般", results[2]["snippet"])   # B

    def test_zero_reading(self):
        results = _parse_cls(SAMPLE_CLS)
        # Third item has reading_num=0, snippet should not show reading count
        self.assertNotIn("👁", results[2]["snippet"])

    def test_empty_data(self):
        results = _parse_cls({"errno": 0, "data": {"roll_data": []}})
        self.assertEqual(results, [])

    def test_missing_content_skipped(self):
        data = {"errno": 0, "data": {"roll_data": [{"ctime": 123}]}}
        results = _parse_cls(data)
        self.assertEqual(results, [])

    def test_no_url_when_no_id(self):
        data = {"errno": 0, "data": {"roll_data": [{"title": "Test", "content": "Body"}]}}
        results = _parse_cls(data)
        self.assertEqual(results[0]["url"], "")


class TestHotlistEngine(unittest.TestCase):
    def test_init(self):
        engine = HotlistEngine(timeout=10)
        self.assertEqual(engine.name, "hotlist")

    def test_default_platforms(self):
        engine = HotlistEngine()
        self.assertEqual(engine.name, "hotlist")


if __name__ == "__main__":
    unittest.main()
