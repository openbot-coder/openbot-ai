"""Tests for web_fetch SSRF protection and untrusted content marking."""

from __future__ import annotations

import json
import socket
from unittest.mock import patch

import primp
import pytest

from openbot.agent.tools import web as web_module
from openbot.agent.tools.web import WebFetchTool
from openbot.config.schema import WebFetchConfig
from openbot.security.workspace_access import (
    bind_workspace_scope,
    build_workspace_scope,
    reset_workspace_scope,
)

_REAL_GETADDRINFO = socket.getaddrinfo


def _fake_resolve_private(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]


def _fake_resolve_public(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_ip():
    tool = WebFetchTool()
    with patch("openbot.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = await tool.execute(url="http://169.254.169.254/computeMetadata/v1/")
    data = json.loads(result)
    assert "error" in data
    assert "private" in data["error"].lower() or "blocked" in data["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_blocks_localhost():
    tool = WebFetchTool()
    def _resolve_localhost(hostname, port, family=0, type_=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    with patch("openbot.security.network.socket.getaddrinfo", _resolve_localhost):
        result = await tool.execute(url="http://localhost/admin")
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_web_fetch_blocks_localhost_even_in_full_workspace_scope(tmp_path):
    tool = WebFetchTool()
    scope = build_workspace_scope(tmp_path, "full")

    def _resolve_localhost(hostname, port, family=0, type_=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]

    token = bind_workspace_scope(scope)
    try:
        with patch("openbot.security.network.socket.getaddrinfo", _resolve_localhost):
            result = await tool.execute(url="http://localhost/admin")
    finally:
        reset_workspace_scope(token)
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_web_fetch_result_contains_untrusted_flag(monkeypatch):
    """When fetch succeeds, result JSON must include untrusted=True and the banner."""
    tool = WebFetchTool()

    fake_html = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"

    class FakeResponse:
        status_code = 200
        url = "https://example.com/page"
        text = fake_html
        headers = {"content-type": "text/html"}
        is_redirect = False
        def raise_for_status(self): pass
        def json(self): return {}
        async def aclose(self): pass

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def get(self, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(web_module.primp, "AsyncClient", FakeClient)

    with patch("openbot.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/page")

    data = json.loads(result)
    assert data.get("untrusted") is True
    assert "[External content" in data.get("text", "")


@pytest.mark.asyncio
async def test_web_fetch_can_skip_jina_and_use_custom_user_agent(monkeypatch):
    tool = WebFetchTool(
        config=WebFetchConfig(use_jina_reader=False),
        user_agent="openbot-test-agent",
    )
    seen_headers: list[dict] = []

    class FakeResponse:
        status_code = 200
        url = "https://example.com/page"
        text = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"
        headers = {"content-type": "text/html"}
        is_redirect = False

        def raise_for_status(self):
            return None

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, **kwargs):
            seen_headers.append(headers or {})
            return FakeResponse()

    monkeypatch.setattr(tool, "_extract_readable_html", lambda html, mode: "Hello world")
    monkeypatch.setattr(web_module.primp, "AsyncClient", FakeClient)

    with patch("openbot.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/page")

    data = json.loads(result)
    assert data["extractor"] == "readability"
    # Single request (no pre-fetch, no Jina) — exactly one header seen
    assert len(seen_headers) == 1
    assert [headers["User-Agent"] for headers in seen_headers] == [
        "openbot-test-agent",
    ]


@pytest.mark.asyncio
async def test_web_fetch_falls_back_when_readability_dependency_is_missing(monkeypatch):
    tool = WebFetchTool(config=WebFetchConfig(use_jina_reader=False))

    class FakeResponse:
        status_code = 200
        url = "https://example.com/page"
        text = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, follow_redirects=False, **kwargs):
            return FakeResponse()

    def _missing_readability(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'lxml_html_clean'")

    monkeypatch.setattr(tool, "_extract_readable_html", _missing_readability)
    monkeypatch.setattr(web_module.primp, "AsyncClient", FakeClient)

    with patch("openbot.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/page")

    data = json.loads(result)
    assert data["extractor"] == "html"
    assert data["untrusted"] is True
    assert "Hello world" in data["text"]


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_redirect_before_readability_request(monkeypatch):
    tool = WebFetchTool(config=WebFetchConfig(use_jina_reader=False))
    requested: list[str] = []

    class FakeStreamResponse:
        status_code = 200
        headers = {"content-type": "text/html"}
        url = "https://attacker.example/start"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aread(self):
            raise AssertionError("non-image prefetch body should not be read")

    class FakeRedirectResponse:
        status_code = 302
        headers = {"location": "http://127.0.0.1:8765/metadata"}
        url = "https://attacker.example/start"

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, **kwargs):
            requested.append(url)
            if url == "http://127.0.0.1:8765/metadata":
                raise AssertionError("private redirect target should not be requested")
            return FakeRedirectResponse()

    monkeypatch.setattr(web_module.primp, "AsyncClient", FakeClient)

    def resolve_public_start_only(hostname, port, family=0, type_=0):
        if hostname == "attacker.example":
            return _fake_resolve_public(hostname, port, family, type_)
        return _REAL_GETADDRINFO(hostname, port, family, type_)

    with patch("openbot.security.network.socket.getaddrinfo", resolve_public_start_only):
        result = await tool.execute(url="https://attacker.example/start")

    data = json.loads(result)
    assert "error" in data
    assert "redirect blocked" in data["error"].lower()
    assert requested == ["https://attacker.example/start"]


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_redirect_before_returning_image(monkeypatch):
    """When an image URL redirects to a private IP, the redirect must be blocked."""
    tool = WebFetchTool(config=WebFetchConfig(use_jina_reader=False))

    # First call (streaming prefetch) -> 302 redirect to private IP
    # Second call (non-streaming for redirect validation) -> 302 again
    # The safe redirect loop should block at validation step
    class FakeRedirectResponse:
        status_code = 302
        headers = {"location": "http://127.0.0.1/secret.png"}
        url = "https://example.com/image.png"

        def raise_for_status(self):
            return None

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, stream=False, **kwargs):
            if url == "https://example.com/image.png":
                return FakeRedirectResponse()
            raise AssertionError(f"private redirect target should not be requested: {url}")

    monkeypatch.setattr(web_module.primp, "AsyncClient", FakeClient)

    def resolve_public_start_only(hostname, port, family=0, type_=0):
        if hostname == "example.com":
            return _fake_resolve_public(hostname, port, family, type_)
        return _REAL_GETADDRINFO(hostname, port, family, type_)

    with patch("openbot.security.network.socket.getaddrinfo", resolve_public_start_only):
        result = await tool.execute(url="https://example.com/image.png")

    data = json.loads(result)
    assert "error" in data
    assert "redirect blocked" in data["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_does_not_request_private_redirect_target(monkeypatch):
    """When a URL redirects to a private IP, the private target must never be fetched."""
    tool = WebFetchTool(config=WebFetchConfig(use_jina_reader=False))
    requested: list[str] = []

    class FakeRedirectResponse:
        status_code = 302
        headers = {"location": "http://127.0.0.1:8765/metadata"}
        url = "https://attacker.example/start"

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, **kwargs):
            requested.append(url)
            if url == "http://127.0.0.1:8765/metadata":
                raise AssertionError("private redirect target should not be requested")
            return FakeRedirectResponse()

    monkeypatch.setattr(web_module.primp, "AsyncClient", FakeClient)

    def resolve_public_start_only(hostname, port, family=0, type_=0):
        if hostname == "attacker.example":
            return _fake_resolve_public(hostname, port, family, type_)
        return _REAL_GETADDRINFO(hostname, port, family, type_)

    with patch("openbot.security.network.socket.getaddrinfo", resolve_public_start_only):
        result = await tool.execute(url="https://attacker.example/start")

    data = json.loads(result)
    assert "error" in data
    assert "redirect blocked" in data["error"].lower()
    assert requested == ["https://attacker.example/start"]
