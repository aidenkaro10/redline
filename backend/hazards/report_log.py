"""
Append-only report log: every hazard ever reported at a site gets a record.

One JSON line per change, in data/hazards/hazard_log.jsonl:
    reported   first time we saw this event
    updated    its severity or status changed (lightning countdown, river rising)
    cleared    it's gone from the feed
    injected   a demo test event was added by hand

Lines are only ever added, never edited or deleted, so the site keeps a
record of what was reported and when.

    log = HazardLog()
    log.sync(events)                      # call after each refresh
    log.read(site_id="eia-663")           # history for one site
"""

import json
import threading

from backend.hazards import store
from backend.hazards.events import iso, utc_now


class HazardLog:
    def __init__(self, path=None):
        self.path = path or (store.data_dir() / "hazard_log.jsonl")
        self._lock = threading.Lock()
        # event_id -> the last state we logged, for events not yet cleared
        self._open = {}
        self._replay()

    def _replay(self):
        """Rebuild which events are still open from the existing file."""
        try:
            with open(self.path) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue       # skip a half-written last line
                    eid = rec["event"]["event_id"]
                    if rec["kind"] == "cleared":
                        self._open.pop(eid, None)
                    else:
                        self._open[eid] = rec["event"]
        except OSError:
            pass

    def _append(self, kind, event_dict, at):
        rec = {"logged_at": iso(at), "kind": kind, "event": event_dict}
        with open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    def record(self, kind, event, at=None):
        """Log one event by hand (used for demo injections)."""
        with self._lock:
            d = event.to_dict() if hasattr(event, "to_dict") else event
            rec = self._append(kind, d, at or utc_now())
            if kind == "cleared":
                self._open.pop(d["event_id"], None)
            else:
                self._open[d["event_id"]] = d
            return rec

    def sync(self, events, at=None, skip_clear_sources=()):
        """Compare the current events with what's open and log the changes.

        skip_clear_sources: sources whose feed is down right now. Their events
        aren't marked cleared just because we couldn't reach the feed.
        Returns the list of new log records.
        """
        at = at or utc_now()
        new = []
        with self._lock:
            current = {}
            for ev in events:
                d = ev.to_dict() if hasattr(ev, "to_dict") else ev
                current[d["event_id"]] = d
            for eid, d in current.items():
                old = self._open.get(eid)
                if old is None:
                    new.append(self._append("reported", d, at))
                elif (old["severity"], old["status"]) != (d["severity"], d["status"]):
                    new.append(self._append("updated", d, at))
                self._open[eid] = d
            for eid in list(self._open):
                if eid in current:
                    continue
                d = self._open[eid]
                if d.get("source") in skip_clear_sources:
                    continue
                cleared = dict(d, status="cleared", band_pattern=None)
                new.append(self._append("cleared", cleared, at))
                del self._open[eid]
        return new

    def read(self, site_id=None, since=None, limit=None):
        """Read log records, newest last. Optional filter by site and start time (ISO)."""
        out = []
        try:
            with open(self.path) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if site_id and rec["event"]["site_id"] != site_id:
                        continue
                    if since and rec["logged_at"] < since:
                        continue
                    out.append(rec)
        except OSError:
            return []
        return out[-limit:] if limit else out


_default_log = None


def get_log():
    """The shared log used by the hazard picture."""
    global _default_log
    if _default_log is None:
        _default_log = HazardLog()
    return _default_log


def reset_log(path=None):
    """Start a fresh log object (tests point it at a temp file)."""
    global _default_log
    _default_log = HazardLog(path) if path else None
