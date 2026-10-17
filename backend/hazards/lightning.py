"""
Lightning: GOES-19 satellite flashes and the 10-mile stop-work rule.

Where the flashes come from
    The GOES-19 Geostationary Lightning Mapper writes a small NetCDF file
    every 20 seconds to the public AWS bucket noaa-goes19 (product
    GLM-L2-LCFA). No key. We list new files over plain HTTPS, download them,
    and keep the Florida flashes from the last ~3 hours.
    Location error is about 4 km. Since July 2026 GOES-19 has extra false
    flashes, so we keep only flash_quality_flag == 0 and need 2+ flashes.

The rule (constants in backend/config.py, never learned)
    - STOP WORK when LIGHTNING_MIN_FLASHES (2) good flashes land within
      LIGHTNING_STOP_RADIUS_MI (10 mi) of the site within
      LIGHTNING_CONFIRM_WINDOW_MIN (30 min) of each other.
    - The all-clear comes LIGHTNING_ALL_CLEAR_MIN (30 min) after the last
      flash within 10 miles. Every new flash restarts the countdown.
    - If the lightning feed is down, we never give the all-clear: we can't
      know there were no new strikes.
    - Flashes within LIGHTNING_WATCH_RADIUS_MI (30 mi) show as "nearby".

    from backend.hazards.lightning import lightning_events
    events, states, feed = lightning_events(sites)
    states["eia-663"]   # {"state": "stop_work", "minutes_to_all_clear": 23.5, ...}
"""

import json
import logging
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from backend.config import (LIGHTNING_ALL_CLEAR_MIN, LIGHTNING_CONFIRM_WINDOW_MIN,
                            LIGHTNING_FEED_STALE_MIN, LIGHTNING_GOOD_QUALITY_FLAG,
                            LIGHTNING_MIN_FLASHES, LIGHTNING_STOP_RADIUS_MI,
                            LIGHTNING_WATCH_RADIUS_MI)
from backend.hazards import fake, store
from backend.hazards.cache import FeedResult
from backend.hazards.events import HazardEvent, iso, parse_time, utc_now  # noqa: F401
from backend.hazards.geo import distance_mi
from backend.hazards.http import get_bytes, get_text

log = logging.getLogger(__name__)

BUCKET_URL = "https://noaa-goes19.s3.amazonaws.com"
PRODUCT = "GLM-L2-LCFA"
SOURCE = "GOES-19 GLM"

# Keep flashes in a box around Florida (30+ miles past the coast)
FL_BOX = {"lat": (23.5, 32.0), "lon": (-88.7, -79.0)}
BACKFILL_MIN = LIGHTNING_ALL_CLEAR_MIN + 5     # how far back to fetch on startup
KEEP_HOURS = 3                                 # flashes kept in memory
MIN_UPDATE_GAP_S = 20                          # a new file appears every 20 s
DOWNLOAD_THREADS = 6

_KEY_TIME = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)_")


# ---------------------------------------------------------------------------
# Reading GLM files
# ---------------------------------------------------------------------------
def key_start_time(key):
    """Start time from a GLM file name, e.g. ..._s20262901133400_... -> 2026-10-17 11:33:40."""
    m = _KEY_TIME.search(key)
    if not m:
        return None
    year, doy, hh, mm, ss, tenth = (int(g) for g in m.groups())
    return (datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=doy - 1, hours=hh, minutes=mm, seconds=ss + tenth / 10))


def list_glm_keys(start, end):
    """Names of GLM files that started between start and end (UTC)."""
    keys = []
    hour = start.replace(minute=0, second=0, microsecond=0)
    while hour <= end:
        prefix = f"{PRODUCT}/{hour:%Y}/{hour.timetuple().tm_yday:03d}/{hour:%H}/"
        xml = get_text(BUCKET_URL + "/", {"list-type": "2", "prefix": prefix}, timeout=20)
        for key in re.findall(r"<Key>([^<]+)</Key>", xml):
            t = key_start_time(key)
            if t and start <= t <= end:
                keys.append(key)
        hour += timedelta(hours=1)
    return sorted(keys)


def parse_glm(nc_bytes):
    """Flashes from one GLM NetCDF file, inside the Florida box.

    Returns [{"t": epoch seconds, "lat", "lon", "q": quality flag, "e": energy J}].
    """
    import netCDF4          # only needed for live lightning
    ds = netCDF4.Dataset("glm.nc", memory=nc_bytes)
    try:
        lat = ds["flash_lat"][:]
        lon = ds["flash_lon"][:]
        q = ds["flash_quality_flag"][:]
        energy = ds["flash_energy"][:]
        offset = ds["flash_time_offset_of_first_event"]
        # Time units look like "seconds since 2026-10-17 11:33:40.000"
        base = parse_time(offset.units.split("since", 1)[1].strip().replace(" ", "T"))
        secs = offset[:]
        out = []
        for i in range(len(lat)):
            la, lo = float(lat[i]), float(lon[i])
            if not (FL_BOX["lat"][0] <= la <= FL_BOX["lat"][1] and
                    FL_BOX["lon"][0] <= lo <= FL_BOX["lon"][1]):
                continue
            out.append({"t": base.timestamp() + float(secs[i]), "lat": round(la, 4),
                        "lon": round(lo, 4), "q": int(q[i]), "e": float(energy[i])})
        return out
    finally:
        ds.close()


class GLMFeed:
    """Keeps the recent Florida flashes, fetching only files we haven't seen."""

    def __init__(self):
        self._lock = threading.Lock()
        self.flashes = []            # dicts from parse_glm
        self.seen = set()            # file names already read
        self.last_ok = None          # last time a listing + download fully worked
        self.last_try = None
        self.error = None
        self._loaded = False

    def _path(self):
        return store.data_dir() / "cache" / "glm_flashes.json"

    def _load(self):
        self._loaded = True
        try:
            d = json.loads(self._path().read_text())
            self.flashes, self.seen = d["flashes"], set(d["seen"])
            self.last_ok = parse_time(d["last_ok"])
        except (OSError, ValueError, KeyError):
            pass

    def _save(self):
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"flashes": self.flashes, "seen": sorted(self.seen),
                                   "last_ok": iso(self.last_ok)}))
        tmp.replace(path)

    def update(self, now=None):
        """Fetch any new GLM files. Never raises; failures are kept in self.error."""
        now = now or utc_now()
        with self._lock:
            if not self._loaded:
                self._load()
            if self.last_try and (now - self.last_try).total_seconds() < MIN_UPDATE_GAP_S:
                return
            self.last_try = now
            try:
                keys = [k for k in list_glm_keys(now - timedelta(minutes=BACKFILL_MIN), now)
                        if k not in self.seen]
                with ThreadPoolExecutor(DOWNLOAD_THREADS) as pool:
                    blobs = list(pool.map(lambda k: get_bytes(f"{BUCKET_URL}/{k}", timeout=30), keys))
                for key, blob in zip(keys, blobs):
                    self.flashes.extend(parse_glm(blob))
                    self.seen.add(key)
                self.last_ok, self.error = now, None
            except Exception as e:       # WiFi down, S3 hiccup, bad file
                self.error = f"{type(e).__name__}: {e}"[:300]
                log.warning("GLM update failed: %s", self.error)
            cutoff = (now - timedelta(hours=KEEP_HOURS)).timestamp()
            self.flashes = [f for f in self.flashes if f["t"] >= cutoff]
            keep_after = now - timedelta(hours=KEEP_HOURS)
            self.seen = {k for k in self.seen if (key_start_time(k) or now) >= keep_after}
            try:
                self._save()
            except OSError:
                pass

    def result(self, now):
        stale = self.last_ok is None or \
            (now - self.last_ok) > timedelta(minutes=LIGHTNING_FEED_STALE_MIN)
        return FeedResult(None, iso(self.last_ok), stale=stale, error=self.error)


glm_feed = GLMFeed()

# Demo strikes added by inject_strikes (kept apart from real flashes)
_injected_lock = threading.Lock()
_injected = []


def inject_strikes(site, distance_mi=3.0, n=LIGHTNING_MIN_FLASHES, now=None):
    """Drop n fake good-quality flashes distance_mi east of a site, ending now.

    The real rule then runs on them: stop-work starts, and the 30-minute
    all-clear counts down from the last one.
    """
    from backend.hazards.report_log import get_log
    now = now or utc_now()
    dlon = distance_mi / (69.0 * math.cos(math.radians(site.lat)))
    flashes = [{"t": (now - timedelta(seconds=20 * (n - 1 - i))).timestamp(),
                "lat": site.lat, "lon": round(site.lon + dlon, 4), "q": 0, "e": 0.0,
                "fake": True} for i in range(n)]
    with _injected_lock:
        _injected.extend(flashes)
    ev = HazardEvent(site_id=site.site_id, hazard="lightning", severity="danger",
                     title=f"TEST: {n} lightning flashes {distance_mi:g} mi away",
                     source="demo injection", source_id=f"strike-{int(now.timestamp())}",
                     observed_at=iso(now), fetched_at=iso(now), fake=True,
                     details={"distance_mi": distance_mi, "flashes": n})
    get_log().record("injected", ev, at=now)
    return ev.to_dict()


def clear_injected_strikes():
    with _injected_lock:
        _injected.clear()


def injected_flashes():
    with _injected_lock:
        return list(_injected)


MAX_STRIKES_LISTED = 50


def iso_s(epoch_s):
    return iso(datetime.fromtimestamp(epoch_s, tz=timezone.utc))


# ---------------------------------------------------------------------------
# The stop-work rule (pure function: easy to test)
# ---------------------------------------------------------------------------
def site_lightning_state(site, flashes, now, feed_ok_at):
    """Apply the 10-mile / 2-flash / 30-minute rule for one site.

    flashes: [{"t": epoch s, "lat", "lon", "q"}], any order.
    feed_ok_at: last time the lightning feed was confirmed working (datetime).
    """
    now_s = now.timestamp()
    confirm_s = LIGHTNING_CONFIRM_WINDOW_MIN * 60
    clear_s = LIGHTNING_ALL_CLEAR_MIN * 60
    good = [f for f in flashes if f["q"] == LIGHTNING_GOOD_QUALITY_FLAG and f["t"] <= now_s]
    with_d = [(f["t"], distance_mi(site.lat, site.lon, f["lat"], f["lon"])) for f in good]
    near = sorted(t for t, d in with_d if d <= LIGHTNING_STOP_RADIUS_MI)

    # Walk the nearby flashes in time order
    active, started, last, window = False, None, None, []
    for t in near:
        if active and t - last >= clear_s:
            active, started = False, None          # a full quiet 30 minutes passed
        window = [w for w in window if t - w < confirm_s] + [t]
        if not active and len(window) >= LIGHTNING_MIN_FLASHES:
            active, started = True, t
        last = t
    all_clear_at = last + clear_s if last is not None else None
    if active and now_s >= all_clear_at:
        active = False

    # Safety: if the feed was down when the countdown ran out, we never saw
    # those 30 quiet minutes, so hold the stop.
    feed_ok_s = feed_ok_at.timestamp() if feed_ok_at else None
    unconfirmed_clear = (not active and started is not None and all_clear_at is not None
                         and (feed_ok_s is None or feed_ok_s < all_clear_at))
    recent = [(t, d) for t, d in with_d if now_s - t <= confirm_s]
    nearest = min((d for _, d in recent), default=None)
    near_recent = [t for t, d in recent if d <= LIGHTNING_STOP_RADIUS_MI]

    if active or unconfirmed_clear:
        state = "stop_work"
    elif near_recent:
        state = "unconfirmed"          # 1 flash within 10 mi, waiting for a 2nd
    elif nearest is not None and nearest <= LIGHTNING_WATCH_RADIUS_MI:
        state = "nearby"
    else:
        state = "clear"
    ts = lambda s: iso(datetime.fromtimestamp(s, tz=timezone.utc)) if s else None  # noqa: E731
    return {
        "state": state,
        "stop_started_at": ts(started) if state == "stop_work" else None,
        "last_strike_at": ts(last) if last else None,
        "all_clear_at": ts(all_clear_at) if state == "stop_work" else None,
        "minutes_to_all_clear": (round(max(0.0, all_clear_at - now_s) / 60, 1)
                                 if state == "stop_work" else None),
        "all_clear_unconfirmed": bool(unconfirmed_clear),
        "nearest_mi_30min": round(nearest, 1) if nearest is not None else None,
        "flashes_10mi_30min": len(near_recent),
        "flashes_30mi_30min": sum(1 for _, d in recent if d <= LIGHTNING_WATCH_RADIUS_MI),
        # Good flashes within 30 mi in the last 30 min, newest first (for the map ring)
        "strikes": [{"lat": f["lat"], "lon": f["lon"], "at": iso_s(f["t"]),
                     "miles": round(distance_mi(site.lat, site.lon, f["lat"], f["lon"]), 1)}
                    for f in sorted(good, key=lambda f: -f["t"])
                    if now_s - f["t"] <= confirm_s
                    and distance_mi(site.lat, site.lon, f["lat"], f["lon"]) <= LIGHTNING_WATCH_RADIUS_MI
                    ][:MAX_STRIKES_LISTED],
    }


def state_to_event(site, st, result, fake_mode):
    """Turn a site's lightning state into a HazardEvent (or None if clear)."""
    if st["state"] == "clear":
        return None
    common = dict(site_id=site.site_id, hazard="lightning", source=SOURCE,
                  fetched_at=result.fetched_at, stale=result.stale, fake=fake_mode,
                  observed_at=st["last_strike_at"], details=st)
    if st["state"] == "stop_work":
        if st["all_clear_unconfirmed"]:
            title = "Lightning feed down: can't confirm all-clear, STAY SHELTERED"
            status = "all_clear_pending"
        else:
            mins = st["minutes_to_all_clear"]
            near = st["nearest_mi_30min"]
            title = (f"Lightning within {LIGHTNING_STOP_RADIUS_MI} mi"
                     + (f" ({near} mi)" if near is not None else "")
                     + f": STOP WORK, all-clear in {mins:.0f} min")
            # Storm still striking (last flash < 5 min ago) vs quietly counting down
            minutes_since_last = LIGHTNING_ALL_CLEAR_MIN - mins
            status = "active" if minutes_since_last < 5 else "all_clear_pending"
        # source_id = when this stop started, so each storm is one logged event
        return HazardEvent(severity="danger", title=title, status=status,
                           source_id=f"stop-{st['stop_started_at']}",
                           starts=st["stop_started_at"], ends=st["all_clear_at"], **common)
    if st["state"] == "unconfirmed":
        return HazardEvent(severity="advisory", source_id="unconfirmed",
                           title=f"1 lightning flash {st['nearest_mi_30min']} mi away (waiting to confirm)",
                           **common)
    return HazardEvent(severity="advisory", source_id="nearby",
                       title=f"Lightning {st['nearest_mi_30min']} mi away", **common)


def lightning_events(sites, now=None):
    """Run the rule for every site. Returns (events, {site_id: state}, FeedResult)."""
    now = now or utc_now()
    if store.is_fake():
        flashes = fake.storm_flashes(sites, now)
        result = FeedResult(None, iso(now), stale=False)
        feed_ok_at = now
    else:
        glm_feed.update(now)
        flashes = list(glm_feed.flashes)
        result = glm_feed.result(now)
        feed_ok_at = glm_feed.last_ok
    flashes = flashes + injected_flashes()
    states, events = {}, []
    for site in sites:
        st = site_lightning_state(site, flashes, now, feed_ok_at)
        states[site.site_id] = st
        ev = state_to_event(site, st, result, store.is_fake())
        if ev:
            events.append(ev)
    return events, states, result
