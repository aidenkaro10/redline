"""
Polite downloading with a disk cache.

Every web call in the hazard index goes through get_json() or get_file().
The first call saves the answer to disk. Every later call reads the saved copy,
so we never ask IEM or Open-Meteo for the same thing twice.

Rules we follow (IEM asks heavy users not to hammer it):
  - wait at least POLITE_DELAY_S between real network calls
  - send a User-Agent that says who we are
  - retry a few times, slowly, if the server is busy

Where the files go: data/raw/hazard_index/ in the MAIN checkout (gitignored).
Set HAZARD_DATA_DIR in .env to put them somewhere else.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

POLITE_DELAY_S = 1.5      # seconds between real network calls
RETRIES = 4
USER_AGENT = os.getenv("NWS_USER_AGENT", "planit-control student project (SwampHacks XII)")

_last_call = 0.0          # when we last hit the network (for the delay)


def _repo_root() -> Path:
    """The main checkout's folder, even when this code runs inside a git worktree."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        # A worktree lives at <main>/.claude/worktrees/<name>/...
        if parent.name == "worktrees" and parent.parent.name == ".claude":
            return parent.parent.parent
    return here.parents[2]    # ml/hazard_index/fetch.py -> repo root


def data_dir() -> Path:
    """Folder for raw downloads and the cache (never committed)."""
    d = Path(os.getenv("HAZARD_DATA_DIR", _repo_root() / "data" / "raw" / "hazard_index"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(url: str, suffix: str) -> Path:
    key = hashlib.sha1(url.encode()).hexdigest()[:20]
    folder = data_dir() / "cache"
    folder.mkdir(exist_ok=True)
    return folder / f"{key}{suffix}"


def _download(url: str, timeout: float = 120) -> bytes:
    """One real network call, with the polite delay and slow retries."""
    global _last_call
    for attempt in range(RETRIES):
        wait = POLITE_DELAY_S - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as e:
            # 4xx means our request is wrong; retrying won't help
            if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429:
                raise
            if attempt == RETRIES - 1:
                raise
            time.sleep(10 * (attempt + 1))     # back off: 10 s, 20 s, 30 s
    raise RuntimeError("unreachable")


def get_json(base: str, params: dict | None = None, offline: bool = False) -> dict:
    """GET a JSON url, cached on disk. offline=True never touches the network."""
    url = base + ("?" + urllib.parse.urlencode(params) if params else "")
    path = _cache_path(url, ".json")
    if path.exists():
        return json.loads(path.read_text())
    if offline:
        raise FileNotFoundError(f"not cached (offline mode): {url}")
    data = json.loads(_download(url))
    path.write_text(json.dumps(data))
    return data


def get_file(url: str, name: str) -> Path:
    """Download a big file once into data/raw/hazard_index/<name>."""
    path = data_dir() / name
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(_download(url, timeout=600))
        tmp.rename(path)
    return path
