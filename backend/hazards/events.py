"""
The one shape every hazard takes, whatever feed it came from.

A HazardEvent says: at this site, this kind of hazard, this bad, from this
source, seen at this time, and which band pattern it would trigger.

    ev = HazardEvent(site_id="eia-663", hazard="lightning", severity="danger",
                     title="Lightning 4 mi away: stop work", source="GOES-19 GLM")
    ev.to_dict()   # JSON-ready, for the API, the map and the briefing
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# Kinds of site hazard. "weather" is for other NWS alerts (wind, tornado, tropical).
HAZARD_TYPES = ("heat", "lightning", "flood", "fire", "soil", "wildlife", "weather")

# Severity, lowest to highest. The number is used for sorting and ranking.
SEVERITY_LEVEL = {"info": 1, "advisory": 2, "warning": 3, "danger": 4}

# Status of an event
#   active             the hazard is happening (or forecast and in effect)
#   all_clear_pending  lightning only: no new strikes, counting down to all-clear
#   cleared            over (only appears in the report log)
STATUSES = ("active", "all_clear_pending", "cleared")


def utc_now():
    """Current time on this laptop, in UTC. Every event is stamped with it."""
    return datetime.now(timezone.utc)


def iso(dt):
    """datetime -> '2026-10-17T14:05:00Z' (or None)."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text):
    """ISO time string (with Z or offset) -> aware datetime in UTC, or None."""
    if not text:
        return None
    if isinstance(text, datetime):
        return text if text.tzinfo else text.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def band_pattern(hazard, severity):
    """Which band pattern (BUZZ:<PATTERN>) this site hazard would trigger, or None.

    Spec (PROJECT.md Alerts table): lightning -> LIGHTNING; an NWS flood or
    fire weather alert at the site -> FLOOD_FIRE. Site heat uses the heat
    yellow pattern as a heads-up (red is reserved for the worker's own core
    temperature). The alert router (session A) makes the final call.
    """
    level = SEVERITY_LEVEL.get(severity, 0)
    if hazard == "lightning" and level >= SEVERITY_LEVEL["warning"]:
        return "LIGHTNING"
    if hazard in ("flood", "fire") and level >= SEVERITY_LEVEL["warning"]:
        return "FLOOD_FIRE"
    if hazard == "heat" and level >= SEVERITY_LEVEL["warning"]:
        return "HEAT_YELLOW"
    if hazard == "weather" and level >= SEVERITY_LEVEL["danger"]:
        return "FLOOD_FIRE"      # tornado / hurricane warnings: take shelter
    return None


@dataclass
class HazardEvent:
    site_id: str
    hazard: str                     # one of HAZARD_TYPES
    severity: str                   # one of SEVERITY_LEVEL
    title: str                      # short line for the map and the band screen
    source: str                     # "NWS", "GOES-19 GLM", "Open-Meteo", "NWPS", ...
    source_id: str = ""             # the source's own id (NWS alert id, gauge id, ...)
    status: str = "active"
    starts: str = None              # when the hazard starts / started (ISO, UTC)
    ends: str = None                # when it's expected to end, if known
    observed_at: str = None         # when the source says it happened
    fetched_at: str = None          # when this laptop got it
    stale: bool = False             # True if the feed is down and this is the last good copy
    fake: bool = False              # True if from fake mode or a demo injection
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.hazard not in HAZARD_TYPES:
            raise ValueError(f"unknown hazard type {self.hazard!r}")
        if self.severity not in SEVERITY_LEVEL:
            raise ValueError(f"unknown severity {self.severity!r}")
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")
        if self.fetched_at is None:
            self.fetched_at = iso(utc_now())

    @property
    def event_id(self):
        """Stable key for this event, so the log can tell new from already-seen."""
        return f"{self.site_id}|{self.hazard}|{self.source}|{self.source_id}"

    @property
    def level(self):
        return SEVERITY_LEVEL[self.severity]

    @property
    def pattern(self):
        return band_pattern(self.hazard, self.severity) if self.status == "active" or \
            (self.hazard == "lightning" and self.status == "all_clear_pending") else None

    def to_dict(self):
        d = asdict(self)
        d["event_id"] = self.event_id
        d["level"] = self.level
        d["band_pattern"] = self.pattern
        return d
