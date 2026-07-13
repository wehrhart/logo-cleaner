"""HTTP layer: retries, per-host politeness, persistent disk cache."""
import hashlib
import json
import threading
import time
from urllib.parse import urlparse

import requests

from . import config

_session_local = threading.local()

_host_lock = threading.Lock()
_host_last = {}          # host -> monotonic time of last request
_host_failures = {}      # host -> consecutive hard failures (circuit breaker)

_SLOW_HOSTS = {"www.bing.com", "html.duckduckgo.com", "lite.duckduckgo.com"}
# Wikimedia etiquette: descriptive tool UA, gentle pacing, generous 429 backoff.
_WIKIMEDIA_HOSTS = {
    "en.wikipedia.org", "www.wikidata.org", "commons.wikimedia.org",
    "upload.wikimedia.org",
}
_FAST_HOSTS = _WIKIMEDIA_HOSTS | {"t2.gstatic.com", "services2.arcgis.com"}
_WIKIMEDIA_UA = ("HospitalLogoPipeline/1.0 (github.com/wehrhart/logo-cleaner; "
                 "healthcare logo research) python-requests")
_HOST_DELAYS = {"www.wikidata.org": 0.6, "en.wikipedia.org": 0.3,
                "commons.wikimedia.org": 0.3, "upload.wikimedia.org": 0.3,
                "t2.gstatic.com": 0.2, "services2.arcgis.com": 0.2}


def _session() -> requests.Session:
    s = getattr(_session_local, "s", None)
    if s is None:
        s = requests.Session()
        # Full Chrome header set: the egress proxy re-originates TLS, so
        # header realism is what determines WAF acceptance.
        s.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })
        _session_local.s = s
    return s


def _throttle(host: str):
    delay = _HOST_DELAYS.get(host, config.PER_HOST_DELAY)
    if host in _SLOW_HOSTS:
        delay = 3.0
    while True:
        with _host_lock:
            last = _host_last.get(host, 0.0)
            now = time.monotonic()
            wait = last + delay - now
            if wait <= 0:
                _host_last[host] = now
                return
        time.sleep(min(wait, delay))


def _cache_paths(url: str):
    h = hashlib.sha1(url.encode()).hexdigest()
    return (config.HTTP_CACHE_DIR / f"{h}.bin", config.HTTP_CACHE_DIR / f"{h}.json")


class FetchResult:
    __slots__ = ("url", "final_url", "status", "content_type", "content", "from_cache", "error")

    def __init__(self, url, final_url=None, status=0, content_type="", content=b"",
                 from_cache=False, error=None):
        self.url = url
        self.final_url = final_url or url
        self.status = status
        self.content_type = content_type
        self.content = content
        self.from_cache = from_cache
        self.error = error

    @property
    def ok(self):
        return self.status == 200 and not self.error

    def text(self):
        try:
            return self.content.decode("utf-8", errors="replace")
        except Exception:
            return ""


def fetch(url: str, cache: bool = True, timeout: float = None, max_bytes: int = 15_000_000) -> FetchResult:
    """GET with disk cache. Terminal statuses (200/4xx) are cached; transport
    errors and 5xx/429 are retried then reported without caching."""
    timeout = timeout or config.HTTP_TIMEOUT
    bin_p, meta_p = _cache_paths(url)
    if cache and meta_p.exists():
        try:
            meta = json.loads(meta_p.read_text())
            content = bin_p.read_bytes() if bin_p.exists() else b""
            return FetchResult(url, meta.get("final_url", url), meta.get("status", 0),
                               meta.get("content_type", ""), content, from_cache=True)
        except Exception:
            pass

    host = urlparse(url).hostname or ""
    with _host_lock:
        if host not in _FAST_HOSTS and _host_failures.get(host, 0) >= 5:
            return FetchResult(url, error=f"circuit_open:{host}")

    headers = {"User-Agent": _WIKIMEDIA_UA} if host in _WIKIMEDIA_HOSTS else None
    retries = config.MAX_RETRIES + (2 if host in _WIKIMEDIA_HOSTS else 0)
    err = None
    for attempt in range(retries + 1):
        _throttle(host)
        try:
            r = _session().get(url, timeout=timeout, stream=True, allow_redirects=True,
                               headers=headers)
            status = r.status_code
            if status == 429 and attempt < retries:
                r.close()
                time.sleep(6 * (attempt + 1))
                continue
            if status in (500, 502, 503, 504) and attempt < retries:
                r.close()
                time.sleep(2 ** (attempt + 1))
                continue
            content = b""
            for chunk in r.iter_content(chunk_size=65536):
                content += chunk
                if len(content) > max_bytes:
                    break
            ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            res = FetchResult(url, str(r.url), status, ct, content)
            with _host_lock:
                _host_failures[host] = 0
            if cache and status in (200, 301, 302, 403, 404, 410) and len(content) <= 3_000_000:
                try:
                    bin_p.write_bytes(content)
                    meta_p.write_text(json.dumps({
                        "url": url, "final_url": str(r.url), "status": status,
                        "content_type": ct, "size": len(content),
                    }))
                except OSError:
                    pass  # disk pressure: serve uncached
            return res
        except requests.RequestException as e:
            err = repr(e)[:200]
            time.sleep(1.5 ** attempt)
    with _host_lock:
        _host_failures[host] = _host_failures.get(host, 0) + 1
    return FetchResult(url, error=err or "unknown_error")


def get_json(url: str, params: dict = None, cache: bool = True):
    if params:
        from urllib.parse import urlencode
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    res = fetch(url, cache=cache)
    if not res.ok:
        return None, res
    try:
        return json.loads(res.content.decode("utf-8", errors="replace")), res
    except Exception:
        return None, res
