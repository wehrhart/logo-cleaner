"""Headless-Chromium fallback for JS-rendered sites.

Uses `chromium --headless --dump-dom` as a subprocess: version-proof, crash-
isolated, and proxy-aware. A semaphore bounds concurrent renders.
"""
import os
import shutil
import subprocess
import threading

CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
_sem = threading.BoundedSemaphore(3)
_available = None


def available() -> bool:
    """Disabled unless explicitly enabled: in this session's environment the
    egress proxy resets Chromium's CONNECT sockets, so renders only ever
    return Chromium's error page. Full browser headers on plain requests
    handle WAF-blocked sites instead (TLS is re-originated by the proxy)."""
    global _available
    if _available is None:
        _available = (os.environ.get("ENABLE_RENDER") == "1"
                      and bool(shutil.which(CHROMIUM) or os.path.exists(CHROMIUM)))
    return _available


def render(url: str, timeout_s: float = 35.0):
    """Render a page and return its post-JS DOM HTML, or None."""
    if not available():
        return None
    proxy = os.environ.get("HTTPS_PROXY", "")
    cmd = [
        CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu",
        "--disable-dev-shm-usage", "--mute-audio", "--hide-scrollbars",
        "--blink-settings=imagesEnabled=false",
        "--virtual-time-budget=6000", "--timeout=20000",
        "--window-size=1366,900", "--dump-dom", url,
    ]
    if proxy:
        cmd.insert(-2, f"--proxy-server={proxy}")
    with _sem:
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
        except (subprocess.TimeoutExpired, OSError):
            return None
    html = out.stdout.decode("utf-8", errors="replace")
    if len(html) < 500 or "<body" not in html.lower():
        return None
    if "Copyright 2017 The Chromium Authors" in html:  # Chromium error page
        return None
    return html
