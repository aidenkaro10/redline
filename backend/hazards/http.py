"""
Plain HTTP GET helpers using only the standard library.

Every call has a timeout, so a dead WiFi connection fails fast instead of
hanging the server. Callers wrap these in a FeedCache to survive outages.
"""

import json
import ssl
import urllib.parse
import urllib.request

from backend.config import NWS_USER_AGENT

DEFAULT_TIMEOUT_S = 20


def _ssl_context():
    """HTTPS certificates. Python from python.org on a Mac ships without them,
    so use certifi's bundle (installed with httpx) when it's there."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL = _ssl_context()


def build_url(url, params=None):
    if not params:
        return url
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)


def get_bytes(url, params=None, headers=None, timeout=DEFAULT_TIMEOUT_S):
    """GET a URL and return the raw bytes. Raises on any network or HTTP error."""
    h = {"User-Agent": NWS_USER_AGENT, "Accept-Encoding": "identity"}
    h.update(headers or {})
    req = urllib.request.Request(build_url(url, params), headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
        return resp.read()


def get_text(url, params=None, headers=None, timeout=DEFAULT_TIMEOUT_S):
    return get_bytes(url, params, headers, timeout).decode("utf-8", errors="replace")


def get_json(url, params=None, headers=None, timeout=DEFAULT_TIMEOUT_S):
    return json.loads(get_bytes(url, params, headers, timeout))
