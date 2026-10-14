"""Web search for general chat, through a SearXNG instance (by default one running on this Mac).
Only the search words leave the machine, and only when the user turns Web on. Vyum talks to SearXNG over HTTP;
SearXNG (AGPL-3.0) runs as its own program, so its licence does not extend to Vyum's code."""
import atexit
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from . import config

DEFAULT_SEARXNG = "http://127.0.0.1:8888"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/130.0 Safari/537.36")
_lock = threading.Lock()


# settings -----------------------------------------------------------------------
def _settings_file():
    return config.DATA_DIR / "settings.json"


def settings() -> dict:
    try:
        saved = json.loads(_settings_file().read_text())
    except (OSError, json.JSONDecodeError):
        saved = {}
    return {"searxng_url": saved.get("searxng_url") or DEFAULT_SEARXNG}


def save_settings(patch: dict) -> dict:
    url = (patch.get("searxng_url") or "").strip().rstrip("/") or DEFAULT_SEARXNG
    if urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
        raise ValueError(f"Not a web address: {url}")
    with _lock:
        _settings_file().parent.mkdir(parents=True, exist_ok=True)
        tmp = _settings_file().with_suffix(".tmp")
        tmp.write_text(json.dumps({"searxng_url": url}, indent=1))
        tmp.replace(_settings_file())
    return settings()


def public_settings() -> dict:
    return settings()


# search -------------------------------------------------------------------------
def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.8"},
                        timeout=config.WEB_TIMEOUT_S, follow_redirects=True)


class SearchUnavailable(RuntimeError):
    pass


_local = {"proc": None}


def _is_local_default(base: str) -> bool:
    return base.rstrip("/") == DEFAULT_SEARXNG


def start_local() -> bool:
    """Starts the bundled local SearXNG (scripts/start_searxng.sh) if it is installed. Returns True once it answers."""
    with _lock:
        proc = _local["proc"]
        if proc is None or proc.poll() is not None:
            if not (config.SEARXNG_DIR / ".venv/bin/python").exists():
                return False
            log = open(config.DATA_DIR / "searxng.log", "ab")
            proc = _local["proc"] = subprocess.Popen(["/bin/zsh", str(config.SEARXNG_START)], stdout=log, stderr=log,
                                                     env={**os.environ, "SEARXNG_DIR": str(config.SEARXNG_DIR)})
            atexit.register(proc.terminate)
    for _ in range(40):
        try:
            if httpx.get(DEFAULT_SEARXNG + "/healthz", timeout=1).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def search(query: str, n: int = config.WEB_RESULTS, base: str | None = None) -> list[dict]:
    base = (base or settings()["searxng_url"]).rstrip("/")
    try:
        with _client() as c:
            try:
                r = c.get(base + "/search", params={"q": query, "format": "json"})
            except httpx.ConnectError:
                if not (_is_local_default(base) and start_local()):
                    raise
                r = c.get(base + "/search", params={"q": query, "format": "json"})
    except httpx.HTTPError as e:
        raise SearchUnavailable(f"SearXNG is not reachable at {base} ({type(e).__name__}). Start it, or set its "
                                "address in Settings.") from e
    if r.status_code == 403:
        raise SearchUnavailable(f"SearXNG at {base} refuses JSON results. Add `json` to search.formats in its settings.yml.")
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x["url"], "snippet": x.get("content", "") or ""}
            for x in r.json().get("results", []) if x.get("url")][:n]


def check(base: str | None = None) -> dict:
    """Used by Settings: does this address answer like a SearXNG instance with JSON enabled?"""
    try:
        results = search("vyum test", 3, base)
        return {"ok": True, "results": len(results)}
    except (SearchUnavailable, httpx.HTTPError, ValueError) as e:
        return {"ok": False, "error": str(e)}


# pages --------------------------------------------------------------------------
_DROP = "script, style, noscript, svg, nav, header, footer, aside, form, iframe, button, [role=navigation], [aria-hidden=true]"


def page_text(html: str) -> str:
    """Readable text of a page: the article or main element when there is one, without menus and footers."""
    soup = BeautifulSoup(html, "lxml")
    for el in soup.select(_DROP):
        el.decompose()
    root = soup.select_one("article") or soup.select_one("main") or soup.body or soup
    lines = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "pre", "blockquote"]):
        if el.find(["p", "li"]) and el.name not in ("td",):
            continue  # its children are visited on their own
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if len(t) >= 25 or (el.name.startswith("h") and t):
            lines.append(t)
    text = "\n".join(dict.fromkeys(lines))
    return text if len(text) > 200 else re.sub(r"\s+", " ", root.get_text(" ", strip=True))


def fetch(url: str, max_bytes: int = 2_000_000) -> str:
    with _client() as c, c.stream("GET", url) as r:
        r.raise_for_status()
        if "html" not in r.headers.get("content-type", "html"):
            return ""
        body = b""
        for block in r.iter_bytes():
            body += block
            if len(body) > max_bytes:
                break
    return page_text(body.decode(r.encoding or "utf-8", errors="replace"))


def _passages(text: str, size: int = config.CHUNK_CHARS) -> list[str]:
    out, cur = [], ""
    for line in text.splitlines():
        if cur and len(cur) + len(line) > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}".strip()
    if cur:
        out.append(cur)
    return [p[: size * 2] for p in out]


def site(url: str) -> str:
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


def gather(queries: list[str], question: str, rerank) -> tuple[list[dict], list[dict]]:
    """Search, read the top pages in parallel, and return (best passages, search results).
    Falls back to result snippets for pages that cannot be read."""
    results, seen = [], set()
    for q in queries:
        for r in search(q):
            if r["url"] not in seen and urlparse(r["url"]).scheme in ("http", "https"):
                seen.add(r["url"])
                results.append(r)
    top = results[: config.WEB_PAGES]

    def read(r):
        try:
            return r, fetch(r["url"])
        except (httpx.HTTPError, ValueError, UnicodeError):
            return r, ""
    with ThreadPoolExecutor(max_workers=len(top) or 1) as pool:
        pages = list(pool.map(read, top))
    rows = []
    for r, text in pages:
        chunks = _passages(text)[:40] or ([r["snippet"]] if r["snippet"] else [])
        rows += [{"id": f"{r['url']}#{i}", "doc_name": r["title"] or site(r["url"]), "section": site(r["url"]),
                  "text": t, "url": r["url"], "kind": "web"} for i, t in enumerate(chunks)]
    rows += [{"id": r["url"], "doc_name": r["title"] or site(r["url"]), "section": site(r["url"]), "text": r["snippet"],
              "url": r["url"], "kind": "web"} for r in results[config.WEB_PAGES:] if r["snippet"]]
    ranked = rerank(question, rows, config.WEB_PASSAGES * 2) if rows else []
    picked, per_site = [], {}
    for row in ranked:  # at most two passages from one page, so one site cannot crowd out the rest
        if per_site.get(row["url"], 0) < 2:
            per_site[row["url"]] = per_site.get(row["url"], 0) + 1
            picked.append(row)
        if len(picked) == config.WEB_PASSAGES:
            break
    return picked, results
