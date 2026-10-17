"""
A cache for each hazard feed, so the WiFi dropping never blanks the map.

    cache = FeedCache("nws_alerts", max_age_s=120)
    result = cache.get(fetch_alerts)      # fetch_alerts() calls the real API
    result.data, result.stale, result.fetched_at

- If the saved copy is newer than max_age_s, it's returned without a call.
- Otherwise we call the API. If that works, we save and return the new data.
- If the call fails (no internet, API down), we return the last good copy
  marked stale=True, with the error, instead of crashing.

Copies are saved to disk, so a restart without internet still has data.
"""

import json
import logging
import threading
from dataclasses import dataclass
from datetime import timedelta

from backend.hazards import store
from backend.hazards.events import iso, parse_time, utc_now

log = logging.getLogger(__name__)


@dataclass
class FeedResult:
    data: object            # whatever the fetch function returned (JSON-friendly)
    fetched_at: str         # when this data was fetched (ISO, UTC), or None
    stale: bool             # True if this is an old copy because the fetch failed
    error: str = None       # why the last fetch failed, if it did

    def to_dict(self):
        return {"fetched_at": self.fetched_at, "stale": self.stale, "error": self.error}


class FeedCache:
    def __init__(self, name, max_age_s):
        self.name = name
        self.max_age_s = max_age_s
        self._lock = threading.Lock()
        self._data = None
        self._fetched_at = None
        self._loaded = False

    def _path(self):
        return store.data_dir() / "cache" / f"{self.name}.json"

    def _load_from_disk(self):
        """First use: pick up the copy saved by a previous run, if any."""
        self._loaded = True
        try:
            saved = json.loads(self._path().read_text())
            self._data, self._fetched_at = saved["data"], parse_time(saved["fetched_at"])
        except (OSError, ValueError, KeyError):
            pass

    def _save(self):
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": iso(self._fetched_at), "data": self._data}))
        tmp.replace(path)          # atomic swap, so a crash never leaves half a file

    def get(self, fetch, now=None, force=False):
        """Return fresh data if we can, or the last good copy marked stale."""
        now = now or utc_now()
        with self._lock:
            if not self._loaded:
                self._load_from_disk()
            fresh = (self._fetched_at is not None
                     and now - self._fetched_at < timedelta(seconds=self.max_age_s))
            if fresh and not force:
                return FeedResult(self._data, iso(self._fetched_at), stale=False)
            try:
                data = fetch()
            except Exception as e:      # any failure: network, HTTP error, bad JSON
                log.warning("feed %s failed, serving last good copy: %s", self.name, e)
                return FeedResult(self._data, iso(self._fetched_at), stale=True,
                                  error=f"{type(e).__name__}: {e}"[:300])
            self._data, self._fetched_at = data, now
            try:
                self._save()
            except OSError as e:
                log.warning("could not save cache %s: %s", self.name, e)
            return FeedResult(data, iso(now), stale=False)

    def clear(self):
        with self._lock:
            self._data, self._fetched_at, self._loaded = None, None, True
