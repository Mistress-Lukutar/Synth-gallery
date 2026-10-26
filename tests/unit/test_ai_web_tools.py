"""Unit tests for the AI web tools (fetch_url / web_search).

Everything runs offline: HTTP is stubbed at the module's
``_http_request`` seam and DNS only ever sees literal addresses or the
always-resolving ``localhost`` — no external request is attempted.
"""
import json

import pytest

from app.application.services.ai_tools import TOOL_REGISTRY
from app.application.services.ai_tools import web as web_tools
from app.application.services.ai_tools.base import ToolContext, ToolError


def _response(
    body: bytes = b"ok",
    status: int = 200,
    content_type: str = "text/plain",
    url: str = "https://example.com/x",
) -> web_tools.HttpResponse:
    return web_tools.HttpResponse(
        url=url, status=status, content_type=content_type, body=body
    )


def _patch_http(monkeypatch, response):
    calls = []

    def fake(url, data=None):
        calls.append((url, data))
        return response

    monkeypatch.setattr(web_tools, "_http_request", fake)
    return calls


# ============================================================================
# Registry wiring
# ============================================================================

def test_web_tools_registered():
    assert "fetch_url" in TOOL_REGISTRY
    assert "web_search" in TOOL_REGISTRY
    for name in ("fetch_url", "web_search"):
        tool = TOOL_REGISTRY[name]
        assert tool.category == "web"
        # Outbound requests must pause for user confirmation like
        # mutations do (data can leave via the URL/query).
        assert tool.confirmation == "always"


# ============================================================================
# SSRF guard
# ============================================================================

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8008/files",
    "http://localhost/admin",
    "http://10.0.0.5/",
    "http://172.16.1.1/",
    "http://192.168.1.50/",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/",
    "http://[fe80::1]/",
    "http://[fc00::1]/",
])
def test_private_addresses_blocked(url):
    with pytest.raises(ToolError, match="private/internal"):
        web_tools._assert_public_url(url)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/pub",
    "javascript:alert(1)",
    "example.com/no-scheme",
])
def test_non_http_schemes_blocked(url):
    with pytest.raises(ToolError, match="http"):
        web_tools._assert_public_url(url)


def test_unresolvable_host_blocked():
    with pytest.raises(ToolError, match="cannot resolve"):
        web_tools._assert_public_url("http://does-not-exist.invalid/")


def test_public_literal_ip_allowed():
    # Literal public addresses resolve without touching the network.
    web_tools._assert_public_url("http://93.184.216.34/tags.json")
    web_tools._assert_public_url("http://[2606:2800:220:1:248:1893:25c8:1946]/")


# ============================================================================
# HTML -> text extraction
# ============================================================================

def test_html_to_text_drops_scripts_keeps_title():
    html = (
        "<html><head><title>Red fox</title>"
        "<style>body { color: red; }</style></head>"
        "<body><script>steal()</script><h1>Red &amp; white</h1>"
        "<p>Canid of the genus Vulpes.</p></body></html>"
    )
    title, text = web_tools._html_to_text(html)
    assert title == "Red fox"
    assert "steal()" not in text
    assert "color: red" not in text
    assert "Red & white" in text
    assert "Canid of the genus Vulpes." in text


def test_html_to_text_block_tags_become_lines():
    _, text = web_tools._html_to_text("<div>alpha<p>beta</p><li>gamma</li></div>")
    assert text.splitlines() == ["alpha", "beta", "gamma"]


# ============================================================================
# fetch_url executor
# ============================================================================

async def test_fetch_url_returns_html_text_with_title(monkeypatch):
    html = (
        "<html><head><title>red_fox - Danbooru</title></head><body>"
        "<p>A fox with red fur.</p></body></html>"
    ).encode()
    calls = _patch_http(
        monkeypatch,
        _response(html, content_type="text/html; charset=utf-8"),
    )
    result = json.loads(await TOOL_REGISTRY["fetch_url"].executor(
        {"url": "https://danbooru.donmai.us/wiki_pages/red_fox.json"},
        ToolContext(user_id=1),
    ))
    assert result["title"] == "red_fox - Danbooru"
    assert "A fox with red fur." in result["content"]
    assert result["next_offset"] is None
    assert result["status"] == 200
    assert calls[0][0].startswith("https://danbooru.donmai.us/")


async def test_fetch_url_json_passes_through_and_pages(monkeypatch):
    payload = json.dumps([{"name": f"tag_{i}"} for i in range(50)]).encode()
    _patch_http(
        monkeypatch,
        _response(payload, content_type="application/json"),
    )
    first = json.loads(await TOOL_REGISTRY["fetch_url"].executor(
        {"url": "https://e621.net/tags.json", "max_chars": 500},
        ToolContext(user_id=1),
    ))
    assert first["content_type"] == "application/json"
    assert first["content"].startswith("[{")
    assert first["next_offset"] == 500
    assert first["total_chars"] == len(payload)

    second = json.loads(await TOOL_REGISTRY["fetch_url"].executor(
        {
            "url": "https://e621.net/tags.json",
            "max_chars": 500,
            "offset": first["next_offset"],
        },
        ToolContext(user_id=1),
    ))
    assert second["content"] == payload.decode()[500:1000]


async def test_fetch_url_rejects_http_errors_and_binary(monkeypatch):
    _patch_http(monkeypatch, _response(b"gone", status=404))
    with pytest.raises(ToolError, match="HTTP 404"):
        await TOOL_REGISTRY["fetch_url"].executor(
            {"url": "https://example.com/missing"}, ToolContext(user_id=1)
        )

    _patch_http(
        monkeypatch,
        _response(b"\x89PNG...", content_type="image/png"),
    )
    with pytest.raises(ToolError, match="unsupported content type"):
        await TOOL_REGISTRY["fetch_url"].executor(
            {"url": "https://example.com/pic.png"}, ToolContext(user_id=1)
        )


async def test_fetch_url_requires_url():
    with pytest.raises(ToolError, match="url is required"):
        await TOOL_REGISTRY["fetch_url"].executor({}, ToolContext(user_id=1))


# ============================================================================
# DuckDuckGo parsing
# ============================================================================

DDG_HTML = """
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdanbooru.donmai.us%2Fwiki_pages%2Fred_fox&amp;rut=abc">red_fox - <b>Danbooru</b> wiki</a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdanbooru.donmai.us%2Fwiki_pages%2Fred_fox">A <b>fox</b> with red fur, a canid tag on Danbooru.</a>
</div>
"""


def test_parse_ddg_results_decodes_redirect_links():
    results = web_tools._parse_ddg_results(DDG_HTML, limit=5)
    assert len(results) == 1
    entry = results[0]
    assert entry["url"] == "https://danbooru.donmai.us/wiki_pages/red_fox"
    assert entry["title"] == "red_fox - Danbooru wiki"
    assert entry["snippet"].startswith("A fox with red fur")


def test_parse_ddg_results_empty_page():
    assert web_tools._parse_ddg_results("<html><body>anomaly</body></html>", 5) == []


async def test_web_search_executor(monkeypatch):
    calls = _patch_http(
        monkeypatch,
        _response(DDG_HTML.encode(), content_type="text/html"),
    )
    result = json.loads(await TOOL_REGISTRY["web_search"].executor(
        {"query": "danbooru red_fox wiki"}, ToolContext(user_id=1)
    ))
    assert result["query"] == "danbooru red_fox wiki"
    assert len(result["results"]) == 1
    # The search endpoint is hit with a form POST.
    assert calls[0][1] == {"q": "danbooru red_fox wiki"}


async def test_web_search_reports_no_results(monkeypatch):
    _patch_http(
        monkeypatch,
        _response(b"<html><body></body></html>", content_type="text/html"),
    )
    result = json.loads(await TOOL_REGISTRY["web_search"].executor(
        {"query": "xyzzy_nothing"}, ToolContext(user_id=1)
    ))
    assert result["results"] == []
    assert "note" in result


async def test_web_search_requires_query():
    with pytest.raises(ToolError, match="query is required"):
        await TOOL_REGISTRY["web_search"].executor({}, ToolContext(user_id=1))
