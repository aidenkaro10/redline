"""
Tomorrow's 0-10 hazard index for every site (session C2's model), cached once a day.

The model (ml/hazard_index/predict.py) is a model call, not a live feed: scoring
all sites downloads weather and warnings and takes minutes, so we never run it
inside a request. Instead:

- The result is saved per target date, one file in data/hazards/cache/, and
  computed in a background thread. The first call returns what is ready so far
  (maybe nothing) and the picture fills in as sites finish.
- It is recomputed once more after 6 PM local, because that is the cutoff the
  model was trained for (before 6 PM, the missing hours come from the forecast).
- If the model, scikit-learn or the data are missing, or a site fails, that
  site's index is simply None. Nothing else in the hazard picture is affected.
- Fake mode returns believable demo values (shaped like the real output,
  data_mode "mock") with no model and no internet.

    from backend.hazards.index import hazard_indexes
    indexes, status = hazard_indexes(sites)   # {site_id: dict | None}, status dict
"""

import hashlib
import json
import logging
import os
import threading
from datetime import timedelta

from backend.hazards import fake, store
from backend.hazards.events import iso, utc_now

log = logging.getLogger(__name__)

EASTERN_UTC_OFFSET_H = -4        # Florida in daylight time (same simplification as picture.py)
CUTOFF_HOUR_LOCAL = 18           # the model's 6 PM cutoff
HAZARD_KINDS = ("heat", "storm", "flood", "fire", "tropical")
BANDS = ((2.0, "low"), (4.0, "moderate"), (6.0, "high"), (10.01, "extreme"))

_lock = threading.Lock()
_worker = None                   # the background computing thread, if running
_progress = {"key": None, "done": 0, "total": 0, "error": None}
_memory = {}                     # key -> {"computed_at", "model_version", "sites": {site_id: dict|None}}


def local_now(now):
    return now + timedelta(hours=EASTERN_UTC_OFFSET_H)


def tomorrow(now=None):
    """Tomorrow's date in Florida."""
    return (local_now(now or utc_now()) + timedelta(days=1)).date()


def cache_key(now=None):
    """'2026-10-18-am' before 6 PM local, '2026-10-18-pm' after. One computation per key."""
    now = now or utc_now()
    part = "pm" if local_now(now).hour >= CUTOFF_HOUR_LOCAL else "am"
    return f"{tomorrow(now).isoformat()}-{part}"


def _cache_path(key):
    return store.data_dir() / "cache" / f"hazard_index_{key}.json"


def _load(key):
    if key in _memory:
        return _memory[key]
    try:
        _memory[key] = json.loads(_cache_path(key).read_text())
        return _memory[key]
    except (OSError, ValueError):
        return None


def _save(key, data):
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def band_for(index):
    for limit, name in BANDS:
        if index < limit:
            return name
    return "extreme"


# ---------------------------------------------------------------------------
# Real model
# ---------------------------------------------------------------------------
def index_mode():
    """HAZARD_INDEX from .env: auto (cache first, then download) | offline (cache only) | off."""
    mode = os.getenv("HAZARD_INDEX", "auto").strip().lower()
    return mode if mode in ("auto", "offline", "off") else "auto"


def _predict(site_id, day, offline=False):
    """One site from C2's model. offline=True uses only data already downloaded."""
    from ml.hazard_index.predict import predict_index   # heavy import (sklearn, pandas), done lazily
    return predict_index(site_id, day, offline=offline)


def _compute(key, sites, day, predict=None, mode="auto"):
    """Score every site and save after each one. Runs in the background thread.

    Pass 1 uses only cached downloads (fast, no network), so the map fills in
    quickly. Pass 2 (mode auto) downloads what is missing, one site at a time,
    which is polite to the free weather APIs.
    """
    predict = predict or _predict
    data = _memory.setdefault(key, {"computed_at": None, "model_version": None, "sites": {}})
    errors = []

    def finish(site, result, error=None):
        data["sites"][site.site_id] = result
        if result:
            data["model_version"] = result.get("model_version")
        if error:
            errors.append(error)
        with _lock:
            _progress["done"] += 1
            _progress["error"] = errors[0] if errors else None
        try:
            _save(key, data)
        except OSError:
            pass

    todo = [s for s in sites if data["sites"].get(s.site_id) is None]
    _progress["done"] = len(sites) - len(todo)
    missing = []
    for site in todo:
        try:
            finish(site, predict(site.site_id, day, offline=True))
        except FileNotFoundError:
            missing.append(site)          # not downloaded yet (or no model: pass 2 will say so)
        except Exception as e:            # bad data, missing sklearn...: this site gets None
            log.warning("hazard index failed for %s: %s", site.site_id, e)
            finish(site, None, f"{type(e).__name__}: {e}"[:200])
    for site in missing:
        if mode != "auto":
            finish(site, None, "not downloaded yet (HAZARD_INDEX is not auto)")
            continue
        try:
            finish(site, predict(site.site_id, day, offline=False))
        except Exception as e:            # no internet, missing model...: this site gets None
            log.warning("hazard index failed for %s: %s", site.site_id, e)
            finish(site, None, f"{type(e).__name__}: {e}"[:200])
    data["computed_at"] = iso(utc_now())
    try:
        _save(key, data)
    except OSError:
        pass


def _start_worker(key, sites, day, predict=None):
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _progress.update(key=key, done=0, total=len(sites), error=None)
        _worker = threading.Thread(target=_compute, args=(key, sites, day, predict, index_mode()),
                                   name="hazard-index", daemon=True)
        _worker.start()


def wait_for_worker(timeout=None):
    """Block until the background compute finishes (tests and scripts use this)."""
    t = _worker
    if t is not None:
        t.join(timeout)


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
def hazard_indexes(sites, now=None, predict=None):
    """({site_id: index dict or None}, status) for tomorrow.

    Never blocks and never raises. status = {"date", "state", "done", "total",
    "computed_at", "model_version", "error"} where state is one of
    ready | computing | partial | unavailable | fake (unavailable also when HAZARD_INDEX=off).
    """
    now = now or utc_now()
    day = tomorrow(now)
    if store.is_fake():
        return ({s.site_id: fake_index(s, day) for s in sites},
                {"date": day.isoformat(), "state": "fake", "done": len(sites), "total": len(sites),
                 "computed_at": iso(now), "model_version": "mock", "error": None})
    if index_mode() == "off":
        return ({s.site_id: None for s in sites},
                {"date": day.isoformat(), "state": "unavailable", "done": 0, "total": len(sites),
                 "computed_at": None, "model_version": None, "error": "HAZARD_INDEX=off"})
    key = cache_key(now)
    data = _load(key)
    have_all = data is not None and data.get("computed_at") and all(
        data["sites"].get(s.site_id) is not None for s in sites)
    running = _worker is not None and _worker.is_alive() and _progress["key"] == key
    if not have_all and not running and not _gave_up(key, data):
        _start_worker(key, list(sites), day, predict)
        running = True
    got = {s.site_id: (data or {}).get("sites", {}).get(s.site_id) for s in sites}
    n_ready = sum(1 for v in got.values() if v is not None)
    if have_all:
        state = "ready"
    elif running:
        state = "partial" if n_ready else "computing"
    else:
        state = "partial" if n_ready else "unavailable"
    return got, {"date": day.isoformat(), "state": state, "done": n_ready, "total": len(sites),
                 "computed_at": (data or {}).get("computed_at"),
                 "model_version": (data or {}).get("model_version"),
                 "error": _progress["error"] if _progress["key"] == key else None}


def _gave_up(key, data):
    """True if a finished run already tried every site today and some still failed.
    We don't retry failed sites until the next cache key, so a missing model doesn't
    make us hammer the web APIs on every refresh."""
    return bool(data and data.get("computed_at"))


def reset():
    """Forget in-memory state (tests)."""
    global _worker
    wait_for_worker(5)
    _worker = None
    _memory.clear()
    _progress.update(key=None, done=0, total=0, error=None)


def riskiest(entries, limit=10):
    """Sites ranked by tomorrow's index, highest first (sites without an index are left out)."""
    rows = [e for e in entries if e.get("hazard_index")]
    rows.sort(key=lambda e: (-e["hazard_index"]["index"], e["site"]["name"]))
    out = []
    for i, e in enumerate(rows[:limit], 1):
        hi = e["hazard_index"]
        reasons = hi.get("top_reasons") or []
        out.append({"rank": i, "site_id": e["site"]["site_id"], "name": e["site"]["name"],
                    "index": hi["index"], "band": hi["band"],
                    "climatology_index": hi.get("climatology_index"),
                    "top_hazard": max(hi["hazards"], key=lambda k: hi["hazards"][k]["index"])
                    if hi.get("hazards") else None,
                    "top_reason": reasons[0]["text"] if reasons else None})
    return out


# ---------------------------------------------------------------------------
# Fake mode: believable demo values, same shape as the real output
# ---------------------------------------------------------------------------
# (hazard, index) boosts for the fake scenario's demo sites
_DEMO_BOOST = {
    fake.HEAT_SITE: ("heat", 3.6, "Heat index reached 109°F by 6 PM today"),
    fake.STORM_SITE: ("storm", 3.2, "SPC storm outlook for tomorrow: slight risk"),
    fake.FLOOD_SITE: ("flood", 3.4, "Rain over the last 3 days: 3.8 in"),
    fake.FIRE_SITE: ("fire", 2.6, "Dry ground and low humidity forecast tomorrow"),
    fake.SOIL_SITE: ("flood", 2.2, "Soil is nearly saturated after recent rain"),
}


def _h(site_id, salt):
    """A stable 0-1 number for (site, salt), so fake values don't change between runs."""
    return int(hashlib.sha256(f"{site_id}|{salt}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def fake_index(site, day):
    """Demo hazard index for one site. Deterministic, in the real output's shape."""
    clim = {k: round(0.3 + 1.0 * _h(site.site_id, "clim" + k) * (1.4 if k in ("heat", "storm") else 0.6), 2)
            for k in HAZARD_KINDS}
    hz = {k: round(clim[k] * (0.7 + 0.8 * _h(site.site_id, "day" + k)), 2) for k in HAZARD_KINDS}
    reasons = []
    boost = _DEMO_BOOST.get(site.site_id)
    if boost:
        kind, value, text = boost
        hz[kind] = round(max(hz[kind], value), 2)
        reasons.append({"hazard": kind, "feature": "demo", "value": None,
                        "points": round(value - clim[kind], 1), "text": text})
    if site.lat < 27.5 and not boost:
        hz["heat"] = round(hz["heat"] + 0.6, 2)
    top = max(hz, key=hz.get)
    if not reasons and hz[top] > clim[top] + 0.5:
        reasons.append({"hazard": top, "feature": "demo", "value": None,
                        "points": round(hz[top] - clim[top], 1),
                        "text": f"{top.capitalize()} conditions above a usual day (demo data)"})
    overall = round(min(10.0, max(hz.values()) + 0.35 * sorted(hz.values())[-2]), 2)
    clim_overall = round(min(10.0, max(clim.values()) + 0.35 * sorted(clim.values())[-2]), 2)

    def probs(index):
        p_adv = min(0.99, index / 3.6)
        return {"p_advisory": round(p_adv, 3), "p_warning": round(p_adv * 0.45, 3),
                "p_damage": round(p_adv * 0.08, 3)}
    return {
        "site_id": site.site_id, "site_name": site.name, "date": day.isoformat(),
        "index": overall, "band": band_for(overall), "climatology_index": clim_overall,
        **probs(overall),
        "hazards": {k: {"index": hz[k], **probs(hz[k]), "climatology_index": clim[k]}
                    for k in HAZARD_KINDS},
        "top_reasons": reasons,
        "cutoff": f"{(day - timedelta(days=1)).isoformat()}T18:00:00-04:00",
        "data_mode": "mock", "model_version": "mock", "fake": True,
        "notes": ["DEMO numbers from fake mode, not the trained model."],
    }
