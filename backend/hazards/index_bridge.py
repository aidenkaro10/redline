"""
Session C2's 0-10 hazard index, plugged into the hazard picture.

The picture asks index_for(site_id) and gets C2's predict_index() result for
TOMORROW (or None until it's ready). A background thread scores every site:
cached data first (fast, no network), then online for sites with nothing cached
(a few polite web calls per site, done one at a time), and repeats every hour.
It never blocks the picture, and it never makes a safety call: the index only ranks sites.

Settings (.env): HAZARD_INDEX = auto (default: offline first, then online)
                              | offline (cache only, no network) | off
In fake hazard mode (replay demos) it stays offline.
"""

import datetime as dt
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

_results: dict[str, dict] = {}
_lock = threading.Lock()
_thread: threading.Thread | None = None


def tomorrow() -> str:
    """The day the index scores: tomorrow, local date."""
    return (dt.date.today() + dt.timedelta(days=1)).isoformat()


def index_for(site_id: str) -> dict | None:
    """C2's result for this site and tomorrow, or None if it isn't computed yet."""
    with _lock:
        r = _results.get(site_id)
    return r if r and r.get("date") == tomorrow() else None


def refresh_once(online: bool) -> int:
    """Score every site that doesn't have tomorrow's index yet. Returns how many it scored."""
    try:
        from ml.hazard_index.predict import predict_index
        from ml.hazard_index.sites import load_sites
    except Exception as e:                     # ml/ or its model not available on this machine
        log.info("hazard index unavailable: %s", e)
        return 0
    day = tomorrow()
    sites = load_sites()
    done = 0

    def keep(site_id, r):
        nonlocal done
        with _lock:
            _results[site_id] = r
        done += 1

    # Pass 1: everything already cached (fast, no network), so the map fills in quickly.
    missing = []
    for s in sites:
        if index_for(s.site_id):
            continue
        try:
            keep(s.site_id, predict_index(s.site_id, day, offline=True, sites=sites))
        except FileNotFoundError:
            missing.append(s)
        except Exception as e:
            log.debug("hazard index for %s failed: %s", s.site_id, e)
    # Pass 2: download what's missing, one site at a time (polite to the free APIs).
    if online:
        for s in missing:
            try:
                keep(s.site_id, predict_index(s.site_id, day, offline=False, sites=sites))
            except Exception as e:
                log.debug("hazard index for %s failed: %s", s.site_id, e)
    log.info("hazard index: %d sites scored for %s (%d total)", done, day, len(_results))
    return done


def start_background(interval_s: int = 3600, online: bool | None = None) -> threading.Thread | None:
    """Start the hourly scoring thread once. online=None means: follow HAZARD_INDEX."""
    global _thread
    mode = os.getenv("HAZARD_INDEX", "auto").lower()
    if mode == "off" or (_thread and _thread.is_alive()):
        return _thread
    go_online = (mode == "auto") if online is None else online

    def loop():
        while True:
            try:
                refresh_once(go_online)
            except Exception:
                log.exception("hazard index refresh failed")
            time.sleep(interval_s)

    _thread = threading.Thread(target=loop, name="hazard-index", daemon=True)
    _thread.start()
    return _thread
