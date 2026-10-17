"""
Demo hook: inject a test hazard at a site, one click from the dashboard.

    from backend.hazards.inject import inject_test_event
    inject_test_event("eia-663", "flood", severity="warning", minutes=10)
    inject_test_event("eia-663", "lightning")   # fake strikes nearby -> stop-work + countdown

Injected events are marked fake=True, show up in the hazard picture like
real ones, are written to the report log as "injected", and expire on
their own after `minutes`.
"""

import itertools
import threading
from datetime import timedelta

from backend.hazards.events import HazardEvent, iso, utc_now
from backend.hazards.report_log import get_log
from backend.sites.sites import get_site

_lock = threading.Lock()
_injected = []                     # list of (expires_at, HazardEvent)
_counter = itertools.count(1)

DEFAULT_TITLES = {
    "heat": "TEST: Extreme Heat Warning",
    "lightning": "TEST: lightning nearby",
    "flood": "TEST: Flash Flood Warning",
    "fire": "TEST: Red Flag Warning",
    "soil": "TEST: saturated ground",
    "wildlife": "TEST: alligator sighted near site",
    "weather": "TEST: Severe Thunderstorm Warning",
}


def inject_test_event(site_id, hazard, severity="warning", minutes=30, title=None,
                      distance_mi=3.0, now=None):
    """Add a fake hazard at a site. Returns the event as a dict.

    Lightning is special: instead of a plain event, it drops two fake
    flashes `distance_mi` from the site into the lightning tracker, so the
    real 10-mile rule and 30-minute countdown run on them.
    """
    now = now or utc_now()
    site = get_site(site_id)          # raises KeyError for an unknown site
    if hazard == "lightning":
        from backend.hazards.lightning import inject_strikes
        return inject_strikes(site, distance_mi=distance_mi, now=now)
    ev = HazardEvent(
        site_id=site.site_id, hazard=hazard, severity=severity,
        title=title or DEFAULT_TITLES.get(hazard, "TEST hazard"),
        source="demo injection", source_id=f"test-{next(_counter)}",
        starts=iso(now), ends=iso(now + timedelta(minutes=minutes)),
        observed_at=iso(now), fetched_at=iso(now), fake=True,
    )
    with _lock:
        _injected.append((now + timedelta(minutes=minutes), ev))
    get_log().record("injected", ev, at=now)
    return ev.to_dict()


def injected_events(now=None):
    """Injected events that haven't expired yet."""
    now = now or utc_now()
    with _lock:
        _injected[:] = [(exp, ev) for exp, ev in _injected if exp > now]
        return [ev for _, ev in _injected]


def clear_injected():
    """Remove every injected test event (and fake strikes)."""
    with _lock:
        _injected.clear()
    from backend.hazards.lightning import clear_injected_strikes
    clear_injected_strikes()
