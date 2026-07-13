"""Headless-Chromium fallback for JS-rendered sites.

Uses `chromium --headless --dump-dom` as a subprocess: version-proof, crash-
isolated, and proxy-aware. A semaphore bounds concurrent renders.
"""
import os
import shutil
import subprocess
import threading

CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
import os as _os
_sem = threading.BoundedSemaphore(int(_os.environ.get("RENDER_CONCURRENCY", "14")))
_available = None


def available() -> bool:
    global _available
    if _available is None:
        _available = bool(shutil.which(CHROMIUM) or os.path.exists(CHROMIUM))
    return _available


def render(url: str, timeout_s: float = 22.0):
    """Render a page and return its post-JS DOM HTML, or None."""
    if not available():
        return None
    proxy = os.environ.get("HTTPS_PROXY", "")
    cmd = [
        CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu",
        "--disable-dev-shm-usage", "--mute-audio", "--hide-scrollbars",
        "--blink-settings=imagesEnabled=false",
        # the egress proxy's TLS interception cannot complete Chromium's
        # TLS 1.3 handshake; cap at 1.2 (certificate verification stays on)
        "--ssl-version-max=tls1.2",
        "--disable-features=EncryptedClientHello",
        "--virtual-time-budget=3500", "--timeout=12000",
        "--window-size=1366,900", "--dump-dom", url,
    ]
    if proxy:
        cmd.insert(-2, f"--proxy-server={proxy}")
    with _sem:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
            stdout, _ = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            proc.wait()
            return None
        except OSError:
            return None
    html = stdout.decode("utf-8", errors="replace")
    if len(html) < 500 or "<body" not in html.lower():
        return None
    if "Copyright 2017 The Chromium Authors" in html:  # Chromium error page
        return None
    return html
