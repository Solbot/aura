# tools/web_search.py
# Web search and page fetch tools for AURA.
# Uses DuckDuckGo HTML search (no API key required) and requests for page fetching.
# Results are cached in the database so follow-up questions don't require re-fetching.

import html as html_module
import re
import socket
import urllib.parse
from html.parser import HTMLParser

import requests
import tools
import db


# --- Network check ---

def _has_network(timeout=2.0):
    """Quick TCP probe to 8.8.8.8:53 — returns True if network is reachable."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("8.8.8.8", 53))
        sock.close()
        return True
    except OSError:
        return False


# --- DuckDuckGo search ---

def _ddg_search(query, count=5):
    """POST to DuckDuckGo HTML endpoint and parse result titles, URLs, snippets."""
    data = urllib.parse.urlencode({"q": query, "kl": "wt-wt"})
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data=data,
        headers=headers,
        timeout=12,
    )
    resp.raise_for_status()
    html = resp.text

    results = []

    # DDG HTML: <a class="result__a" href="//duckduckgo.com/l/?uddg=URL&...">Title</a>
    title_re = re.compile(
        r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    snippet_re = re.compile(
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    titles_and_urls = title_re.findall(html)
    snippets = snippet_re.findall(html)

    for i, (href, raw_title) in enumerate(titles_and_urls):
        if len(results) >= count:
            break

        # Decode HTML entities (e.g. &amp; → &) then percent-decode
        url = urllib.parse.unquote(html_module.unescape(href))

        # Skip DDG-internal redirect and ad URLs
        if "duckduckgo.com" in url:
            continue

        title   = html_module.unescape(re.sub(r'<[^>]+>', '', raw_title)).strip()
        snippet = html_module.unescape(re.sub(r'<[^>]+>', '', snippets[i])).strip() if i < len(snippets) else ""

        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


# --- HTML text extractor ---

class _TextExtractor(HTMLParser):
    """Strip HTML tags and extract readable text, skipping script/style blocks."""

    _SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "svg"}
    _BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "div",
              "section", "article", "blockquote", "pre", "td", "th", "tr"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK and self._parts and self._parts[-1] != "\n":
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK and self._parts and self._parts[-1] != "\n":
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            text = data.strip()
            if text:
                self._parts.append(text + " ")

    def get_text(self):
        raw = "".join(self._parts)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        raw = re.sub(r" {2,}", " ", raw)
        return raw.strip()


def _extract_title(html):
    import html as _h
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    if m:
        return _h.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
    return ""


def _extract_text(html):
    parser = _TextExtractor()
    parser.feed(html)
    return parser.get_text()


# --- Tool implementations ---

def web_search(query):
    """
    Search the web for current information, news, or facts.
    Returns the top results with titles, URLs, and summaries.
    Results are stored so the user can ask for more detail on any URL.
    """
    if not _has_network():
        return "No network connection available right now."

    try:
        results = _ddg_search(query, count=6)
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return "No results found."

    db.web_search_store(query, results)

    lines = [f'Search results for "{query}":\n']
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")

    return "\n\n".join(lines)


def fetch_page(url):
    """
    Fetch and read the full text content of a web page.
    Use this when the user wants more detail from a search result or shares a URL.
    The page is cached — follow-up questions about the same page don't re-fetch.
    Returns the extracted readable text (truncated at ~4000 characters).
    """
    if not _has_network():
        return "No network connection available right now."

    cached = db.web_cache_get(url, max_age_hours=1)
    if cached:
        return f"[{cached['title']}]\n\n{cached['content']}"

    try:
        resp = requests.get(
            url,
            timeout=12,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        resp.raise_for_status()
    except Exception as e:
        return f"Could not fetch page: {e}"

    title   = _extract_title(resp.text)
    content = _extract_text(resp.text)

    if len(content) > 4000:
        content = content[:4000] + "\n\n[content truncated — ask for more if needed]"

    db.web_cache_store(url, title, content)

    return f"[{title}]\n\n{content}"


# --- Register tools ---

tools.register(
    name="web_search",
    description=(
        "Search the web for current events, news, recent information, or facts you are "
        "not certain about. Returns titles, URLs, and snippets for the top results. "
        "Results are stored so the user can ask follow-up questions. "
        "Use this proactively whenever a question involves anything time-sensitive or "
        "that may have changed since your training."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query, written as a clear and concise phrase.",
            }
        },
        "required": ["query"],
    },
    function=web_search,
    permission=tools.FREE,
)

tools.register(
    name="fetch_page",
    description=(
        "Fetch and read the full text content of a specific web page URL. "
        "Use this when the user wants more detail from a search result, shares a URL, "
        "or asks you to read/summarise a page. Pages are cached for one hour — "
        "follow-up questions about the same URL do not require re-fetching."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL of the page to fetch (must start with http:// or https://).",
            }
        },
        "required": ["url"],
    },
    function=fetch_page,
    permission=tools.FREE,
)
