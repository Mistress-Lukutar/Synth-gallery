"""Web access tools for the chat agent: ``fetch_url`` and ``web_search``.

Both run server-side without API keys:

- ``fetch_url`` GETs one public URL and returns its text: HTML is
  reduced to readable text, JSON/XML and other ``text/*`` payloads pass
  through as-is (booru tag APIs answer with JSON). Private and internal
  network addresses are rejected (SSRF guard), every redirect hop is
  re-validated, payloads are size-capped and results are paged to keep
  tool results under the orchestrator's token budget.
- ``web_search`` queries the keyless DuckDuckGo HTML endpoint and
  returns title / URL / snippet triples.

Executors are coroutines that push the blocking HTTP work into a worker
thread (the orchestrator runs on the event loop). Tests monkeypatch
:data:`_http_request` instead of touching the network.
"""
from __future__ import annotations

import asyncio
import html as html_module
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from .base import ToolContext, ToolError, ToolDef

USER_AGENT = (
    "SynthGallery-AI/1.0 (personal media vault; tag research)"
)
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SEARCH_URL = "https://html.duckduckgo.com/html/"
SEARCH_SNIPPET_MAX_CHARS = 300
SEARCH_QUERY_MAX_CHARS = 300
FETCH_DEFAULT_CHARS = 5000
FETCH_MAX_CHARS = 7000
_QUERY_ERROR_NOTE = (
    "no results (or the search page format changed); rephrase the query"
)


@dataclass
class HttpResponse:
    """Raw response of one validated HTTP request."""

    url: str
    status: int
    content_type: str
    body: bytes


# ============================================================================
# SSRF guard
# ============================================================================

def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for loopback / private / link-local / reserved addresses."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _assert_public_url(url: str) -> None:
    """Reject anything that is not a public http(s) URL.

    The hostname is resolved and every returned address must be public;
    this blocks the vault itself, localhost services and the LAN. The
    check runs again for every redirect hop. (A determined DNS-rebinding
    setup can still race the two resolutions; out of scope for a
    personal vault - the tool also sits behind per-chat confirmation.)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError(
            f"only http(s) URLs are supported, got: {parsed.scheme or '(none)'}"
        )
    host = parsed.hostname
    if not host:
        raise ToolError(f"URL has no host: {url}")
    try:
        infos = socket.getaddrinfo(host, parsed.port or None)
    except socket.gaierror:
        raise ToolError(f"cannot resolve host: {host}")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise ToolError(
                f"blocked: {host} resolves to a private/internal address"
            )


# ============================================================================
# HTTP plumbing (single seam tests monkeypatch)
# ============================================================================

def _http_request(url: str, data: Optional[dict] = None) -> HttpResponse:
    """One validated request; GET unless ``data`` (form POST) is given.

    Redirects are followed manually so every hop passes the SSRF guard.
    """
    for _ in range(MAX_REDIRECTS + 1):
        _assert_public_url(url)
        with httpx.Client(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html, application/json, text/*;q=0.8, */*;q=0.1",
                "Accept-Language": "en",
            },
        ) as client:
            request = client.build_request(
                "POST" if data is not None else "GET", url, data=data
            )
            try:
                response = client.send(request, stream=True)
            except httpx.TimeoutException:
                raise ToolError(
                    f"request timed out after {REQUEST_TIMEOUT.read:g}s"
                )
            except httpx.HTTPError as exc:
                raise ToolError(f"request failed: {exc}")

            try:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    response.close()
                    if not location:
                        raise ToolError(
                            f"HTTP {response.status_code} redirect without "
                            "a Location header"
                        )
                    url = urljoin(url, location)
                    continue
                chunks = []
                size = 0
                # iter_bytes (not iter_raw): httpx then transparently
                # decodes gzip/deflate/brotli transfer encodings.
                for chunk in response.iter_bytes(64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ToolError(
                            f"response exceeds "
                            f"{MAX_RESPONSE_BYTES // (1024 * 1024)} MB"
                        )
                    chunks.append(chunk)
            finally:
                response.close()

        return HttpResponse(
            url=url,
            status=response.status_code,
            content_type=response.headers.get("content-type", ""),
            body=b"".join(chunks),
        )
    raise ToolError(f"too many redirects (>{MAX_REDIRECTS})")


# ============================================================================
# Content extraction
# ============================================================================

class _TextExtractor(HTMLParser):
    """Reduce HTML to readable text: block tags become line breaks,
    script/style/noscript/svg and everything but <title> in <head> is
    dropped, character references are decoded by the parser itself."""

    _SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
    _BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
        "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "th",
        "thead", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._pieces: list[str] = []
        self._skip_depth = 0
        self._in_head = False
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "head":
            self._in_head = True
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "head":
            self._in_head = False
        elif tag == "title":
            self._in_title = False
        elif tag in self._BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data.strip()
            return
        if self._in_head:
            return
        self._pieces.append(data)

    def text(self) -> str:
        lines = []
        for line in "".join(self._pieces).splitlines():
            line = line.strip()
            if line:
                lines.append(line)
        return "\n".join(lines)


def _html_to_text(body: str) -> tuple[str, str]:
    """Return ``(title, text)`` of an HTML document."""
    extractor = _TextExtractor()
    try:
        extractor.feed(body)
        extractor.close()
    except Exception:
        # Malformed markup is fine; keep whatever was collected.
        pass
    return extractor.title, extractor.text()


def _looks_like_json(content_type: str, sample: str) -> bool:
    if "json" in content_type.lower():
        return True
    stripped = sample.lstrip()[:1]
    return stripped in ("{", "[")


# ============================================================================
# fetch_url
# ============================================================================

def _fetch_url_sync(url: str, offset: int, max_chars: int) -> str:
    _assert_public_url(url)
    response = _http_request(url)
    if response.status >= 400:
        raise ToolError(
            f"HTTP {response.status} from {response.url}"
        )
    charset = "utf-8"
    if "charset=" in response.content_type:
        charset = (
            response.content_type.split("charset=", 1)[1]
            .split(";", 1)[0]
            .strip("'\" ")
            or "utf-8"
        )
    sample = response.body[:1024].decode(charset, errors="replace")
    content_type = response.content_type.split(";", 1)[0].strip().lower()

    title = ""
    if "html" in content_type:
        title, text = _html_to_text(
            response.body.decode(charset, errors="replace")
        )
    elif _looks_like_json(content_type, sample):
        text = response.body.decode(charset, errors="replace").strip()
    elif content_type.startswith("text/") or "xml" in content_type:
        text = response.body.decode(charset, errors="replace")
    else:
        raise ToolError(
            f"unsupported content type '{content_type or 'unknown'}'; "
            "only text, HTML and JSON are readable"
        )

    total = len(text)
    chunk = text[offset:offset + max_chars]
    next_offset = offset + len(chunk)
    result = {
        "url": url,
        "fetched_url": response.url,
        "status": response.status,
        "content_type": content_type,
        "title": title or None,
        "total_chars": total,
        "offset": offset,
        "content": chunk,
        "next_offset": next_offset if next_offset < total else None,
    }
    if next_offset < total:
        result["note"] = "page through the rest by echoing next_offset"
    return json.dumps(result, ensure_ascii=False)


async def _tool_fetch_url(args: dict, ctx: ToolContext) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("url is required")
    offset = _clamp_int(args.get("offset"), 0, 10**9, 0)
    max_chars = _clamp_int(
        args.get("max_chars"), 500, FETCH_MAX_CHARS, FETCH_DEFAULT_CHARS
    )
    return await asyncio.to_thread(_fetch_url_sync, url, offset, max_chars)


# ============================================================================
# web_search (keyless DuckDuckGo HTML)
# ============================================================================

_RE_DDG_RESULT = re.compile(
    r"<a[^>]+class=\"[^\"]*result__a[^\"]*\"[^>]+href=\"([^\"]+)\"[^>]*>"
    r"(.*?)</a>",
    re.S | re.I,
)
_RE_DDG_SNIPPET = re.compile(
    r"class=\"[^\"]*result__snippet[^\"]*\"[^>]*>(.*?)</a>", re.S | re.I
)
_RE_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_tags(value: str) -> str:
    return html_module.unescape(_RE_HTML_TAG.sub("", value)).strip()


def _clean_ddg_href(href: str) -> str:
    """Recover the real target URL from a DuckDuckGo redirect link."""
    if "uddg=" not in href:
        if href.startswith("//"):
            return "https:" + href
        return href
    query = urlparse(href).query or href.split("?", 1)[-1]
    targets = parse_qs(query).get("uddg", [])
    return targets[0] if targets else href


def _parse_ddg_results(body: str, limit: int) -> list[dict]:
    results = []
    titles = _RE_DDG_RESULT.findall(body)
    snippets = [
        _strip_tags(s) for s in _RE_DDG_SNIPPET.findall(body)
    ]
    for index, (href, raw_title) in enumerate(titles[:limit]):
        entry = {
            "title": _strip_tags(raw_title),
            "url": _clean_ddg_href(href),
        }
        if index < len(snippets):
            snippet = snippets[index][:SEARCH_SNIPPET_MAX_CHARS]
            if snippet:
                entry["snippet"] = snippet
        results.append(entry)
    return results


def _web_search_sync(query: str, limit: int) -> str:
    response = _http_request(SEARCH_URL, data={"q": query})
    if response.status >= 400:
        raise ToolError(
            f"search is unavailable (HTTP {response.status} from "
            "duckduckgo.com)"
        )
    body = response.body.decode("utf-8", errors="replace")
    results = _parse_ddg_results(body, limit)
    return json.dumps({
        "query": query,
        "results": results,
        **({} if results else {"note": _QUERY_ERROR_NOTE}),
    }, ensure_ascii=False)


async def _tool_web_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("query is required")
    if len(query) > SEARCH_QUERY_MAX_CHARS:
        raise ToolError(
            f"query is too long ({len(query)} chars; "
            f"max {SEARCH_QUERY_MAX_CHARS})"
        )
    limit = _clamp_int(args.get("limit"), 1, 10, 8)
    return await asyncio.to_thread(_web_search_sync, query, limit)


def _clamp_int(value, minimum: int, maximum: int, default: int) -> int:
    """Clamp an optional integer argument into [minimum, maximum]."""
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


# ============================================================================
# Tool definitions (registered in registry.py)
# ============================================================================

FETCH_URL_DEF = ToolDef(
    name="fetch_url",
    description=(
        "Fetch ONE public URL and return its content as text: HTML is "
        "reduced to readable text, JSON/XML passes through unchanged - "
        "ideal for keyless booru tag APIs (danbooru tags.json / "
        "wiki_pages/<tag>.json, e621 tags.json). No JavaScript "
        "rendering; internal/private network addresses are blocked. "
        "Long pages are paged via next_offset. The first call in a "
        "conversation needs the user's confirmation."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": (
                    "Absolute public http(s) URL, e.g. "
                    "https://danbooru.donmai.us/tags.json?"
                    "search[name_matches]=*red_fox*&limit=20"
                ),
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": (
                    "Char offset into the page text; echo next_offset "
                    "from the previous result."
                ),
            },
            "max_chars": {
                "type": "integer",
                "minimum": 500,
                "maximum": 7000,
                "default": 5000,
                "description": "Maximum chars of text to return.",
            },
        },
        "required": ["url"],
    },
    executor=_tool_fetch_url,
    category="web",
    confirmation="always",
)

WEB_SEARCH_DEF = ToolDef(
    name="web_search",
    description=(
        "Keyless web search (DuckDuckGo): returns up to 10 results with "
        "title, URL and snippet. Use it to find pages about a tag, then "
        "read them with fetch_url. Supports operators like site: "
        "(e.g. 'site:danbooru.donmai.us wiki <tag>'). Needs the user's "
        "confirmation once per conversation."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 8,
                "description": "Maximum results to return.",
            },
        },
        "required": ["query"],
    },
    executor=_tool_web_search,
    category="web",
    confirmation="always",
)
