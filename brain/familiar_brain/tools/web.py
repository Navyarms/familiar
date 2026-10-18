"""Web tools: DuckDuckGo HTML search (no API key) and page fetch -> readable text. The only network use in the app
besides model downloads; the whole group can be disabled with settings.web_enabled = false."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .registry import P, ToolContext, tool

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Familiar/0.1 (+local assistant)"


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr", "article", "section"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(doc: str, max_chars: int) -> str:
    p = _Text()
    p.feed(doc)
    text = html.unescape("".join(p.parts))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:max_chars]


def _ddg_url(href: str) -> str:
    # DDG wraps results as //duckduckgo.com/l/?uddg=<encoded url>&rut=...
    if "uddg=" in href:
        q = parse_qs(urlparse(href).query)
        if q.get("uddg"):
            return unquote(q["uddg"][0])
    return href if href.startswith("http") else "https:" + href


async def ddg_search(query: str, limit: int = 5) -> list[dict]:
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15, follow_redirects=True) as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "us-en"})
        r.raise_for_status()
    doc = r.text
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<div class="result\b|<div id="links"|$)', doc, re.S):
        url = _ddg_url(html.unescape(m.group(1)))
        title = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
        snippet = html.unescape(re.sub(r"<[^>]+>", "", sn.group(1))).strip() if sn else ""
        if title and "duckduckgo.com" not in url:
            out.append({"title": title, "url": url, "snippet": snippet[:300]})
        if len(out) >= limit:
            break
    return out


@tool("web_search", group="web",
      description="Search the web. Use it for anything current or time-sensitive (news, weather, prices, sports, 'latest'), "
                  "and for facts you are not sure about. Returns titles, links and snippets.",
      parameters=P(query=("string", "Search query", True), limit=("integer", "results to return, default 5", False)), timeout_s=25)
async def web_search(ctx: ToolContext, query: str, limit: int = 5):
    res = await ddg_search(query, max(1, min(10, limit)))
    if not res:
        return [], f"no results for {query}"
    return res, " | ".join(f"{r['title']}: {r['snippet'][:200]} ({r['url']})" for r in res[:3])


@tool("fetch_page", group="web", description="Fetch a web page and return its readable text (for reading an article or a result from web_search).",
      parameters=P(url=("string", "http(s) URL", True), max_chars=("integer", "default 6000", False)), timeout_s=30)
async def fetch_page(ctx: ToolContext, url: str, max_chars: int = 6000):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        body = r.text if ("text" in ctype or "html" in ctype or "json" in ctype) else ""
    if not body:
        return {"url": url, "text": ""}, "that page isn't text"
    text = html_to_text(body, max(500, min(20000, max_chars))) if "html" in ctype else body[:max_chars]
    return {"url": str(r.url), "text": text}, f"fetched {len(text)} characters from {urlparse(str(r.url)).netloc}"
