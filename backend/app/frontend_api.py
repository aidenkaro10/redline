"""
Adapter for the Next.js dashboard (session E).

The frontend was built against its own contract (frontend/src/lib/contract.ts):
WorkerStatus, Estimate, Alert, ManDown, EmergencyCall, DemoState ... sent over
a WebSocket at /ws/live. The hub speaks plain events (estimate, heat_alert,
band_command, mandown_check ...). This file translates one into the other and
serves the REST paths the frontend calls. Nothing here makes a safety decision:
it only re-shapes what the pipeline already decided.

Privacy (contract's canSee): the crew view never gets raw vitals, estimates or
band commands. Worker view gets only its own worker. Demo view gets everything.

    WS   /ws/live?view=crew|worker|demo&worker_id=...
    GET  /api/state?view=...          snapshot (same as the first /ws/live message)
    GET  /api/workers                 WorkerStatus[]
    GET  /api/workers/{id}/history?view=&minutes=60
    GET  /api/sites, /api/schedule, /api/briefing, /api/reports
    POST /api/workers/{id}/button     /api/alerts/{id}/ack     /api/mandown/{id}/resolve
    POST /api/emergency/{id}/cancel   /api/reports             /api/briefing
    POST /api/demo/test-alert         /api/demo/clear-hazards  /api/demo/replay
    POST /api/demo/scenario           /api/demo/mandown        (/api/demo/threshold lives in main.py)
"""

import asyncio
import itertools
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from backend import config
from backend.app.planner_bridge import schedule_status

Z90 = 1.2816                      # 90th percentile of a normal distribution, in standard deviations
HAZARD_KINDS = ("heat", "lightning", "flood", "fire", "soil", "wildlife")
STATUS = {"clear": "green", "yellow": "yellow", "red": "red"}
MANDOWN_REASON = {"fall": "fall", "heat_red_unacknowledged": "no_response_to_red"}
BAND_SEEN_S = 30                  # band counts as connected if we heard from it this recently
KEEP = 500
PLANNER_BODY_FIELDS = ("forecast_p90_c", "peak_before_c", "peak_after_c")   # planner_change: not for crew
MAX_PHOTO_CHARS = 2_000_000
HR_MISSING_S = 120                 # no heart rate this long -> the card says "Heart rate missing"       # ~1.5 MB JPEG as a base64 data URL


class FrontendAdapter:
    def __init__(self, hub):
        self.hub = hub
        self.subscribers: dict[asyncio.Queue, dict] = {}       # queue -> view filter
        self.alerts: deque = deque(maxlen=200)
        self.active_heat_alert: dict[str, dict] = {}            # worker -> their open heat Alert
        self.active_hazard_alert: dict[tuple, dict] = {}        # (site, kind) -> open hazard Alert
        self.incidents: dict[str, dict] = {}                     # worker -> open man-down / SOS incident
        self.emergency: dict | None = None
        self.band: deque = deque(maxlen=100)
        self.log: deque = deque(maxlen=200)
        self.vitals: dict[str, deque] = {}
        self.estimates: dict[str, deque] = {}
        self.reports: list[dict] = []
        self.briefing: dict | None = None
        self.last_status: dict[str, dict] = {}
        self.ids = itertools.count(1)

    # ------------------------------------------------------------------
    # Hub events in -> contract events out
    # ------------------------------------------------------------------
    def on_event(self, ev: dict) -> None:
        kind, wid = ev["type"], ev.get("worker_id")
        out: list[dict] = []
        if kind == "reading":
            v = {k: ev.get(k) for k in ("worker_id", "timestamp", "hr", "skin_temp", "activity", "tilt")}
            self.vitals.setdefault(wid, deque(maxlen=KEEP * 12)).append(v)
            out.append({"type": "vitals", "vitals": v})
        elif kind == "estimate":
            e = self.estimate(ev)
            self.estimates.setdefault(wid, deque(maxlen=KEEP)).append(e)
            out.append({"type": "estimate", "estimate": e})
        elif kind == "heat_alert":
            out += self._heat_alert(ev)
        elif kind == "heat_ack":
            a = self.active_heat_alert.get(wid)
            if a:
                a["acknowledged_at"] = ev["timestamp"]
                out.append({"type": "alert", "alert": a})
        elif kind == "hazard":
            out += self._hazard(ev)
        elif kind == "band_command":
            b = {"worker_id": wid, "command": ev["line"], "at": ev["timestamp"]}
            self.band.append(b)
            out.append({"type": "band_command", "band": b})
        elif kind.startswith("mandown_"):
            out += self._mandown(ev)
        elif kind == "sos":
            out += self._sos(ev)
        elif kind == "emergency_status":
            out += self._emergency(ev)
        elif kind == "worker_removed":
            self.last_status.pop(wid, None)
        elif kind == "why":
            # A model-based why-line arrived for a minute we already sent: fill it in for
            # snapshots/history (the next estimate carries it live).
            for e in reversed(self.estimates.get(wid, [])):
                e["why"] = ev["why"] or e["why"]
                break
        elif kind == "planner_change":
            # Session B's act-on-forecast feed (upsert by id). Body numbers are dropped for crew in broadcast().
            out.append(dict(ev))
        elif kind == "hr_link":
            # Heart rate link status (Bluetooth searching / connected / stale / lost). No body numbers.
            out.append(dict(ev))
        elif kind == "schedule":
            out.append({"type": "schedule", "schedule": ev["schedule"]})   # no body numbers: every view
            for w in list(self.hub.pipelines):
                out += self._status_if_changed(w)                         # on_break / break_at changed
        elif kind == "offset_update":
            # Not in E's contract yet: passed through as-is ({worker_id, offset_c, confidence, n_checks, ...}).
            out.append({k: v for k, v in ev.items()})
        elif kind in ("demo", "replay"):
            out.append({"type": "demo", "demo": self.demo_state()})

        text = self._log_text(ev)
        if text:
            entry = {"at": ev["timestamp"], "text": text, "source": ev.get("source") or "live system"}
            self.log.append(entry)
            out.append({"type": "log", "entry": entry})

        # Worker card and site card changed?
        if wid and wid in self.hub.pipelines and kind != "reading":
            out += self._status_if_changed(wid)
        if kind in ("hazard", "heat_alert", "mandown_check", "mandown_cancelled", "mandown_resolved", "worker",
                    "site_status_update", "sos"):
            site = ev.get("site_id") or self.hub.workers.get(wid, {}).get("site_id")
            if site:
                out.append({"type": "site_status", "site": self.site_status(site)})
        for msg in out:
            self.broadcast(msg)

    def tick(self) -> None:
        """Twice a second: the clock (for countdowns) and band-connected changes."""
        self.broadcast({"type": "clock", "sim_time": self.sim_time(), "speed": self.speed()})
        for wid in list(self.hub.pipelines):
            for msg in self._status_if_changed(wid):
                self.broadcast(msg)

    # ------------------------------------------------------------------
    # Shapes
    # ------------------------------------------------------------------
    def thresholds(self) -> tuple[float, float]:
        if self.hub.demo:
            return config.DEMO_HEAT_WARNING_C, config.DEMO_HEAT_DANGER_C
        return config.HEAT_WARNING_C, config.HEAT_DANGER_C

    def estimate(self, ev: dict) -> dict:
        warning, danger = self.thresholds()
        core, sd = ev.get("core_temp"), ev.get("core_temp_sd") or 0.0
        fc = ev.get("forecast")
        points = [] if not fc else [
            {"minutes_ahead": m, "p10": fc["p10"][i], "p50": fc["p50"][i], "p90": fc["p90"][i]}
            for i, m in enumerate(fc["minutes"])]
        return {
            "worker_id": ev["worker_id"], "timestamp": ev["timestamp"],
            "core": {"mean": core, "p10": round(core - Z90 * sd, 3), "p90": round(core + Z90 * sd, 3)},
            "kalman": ev.get("kalman"), "forecast": points,
            # Session H's model-based why-line when merged, else a plain heart-rate sentence.
            "why": ev.get("why") or self._why(ev["worker_id"]),
            "thresholds": {"warning": warning, "danger": danger, "demo": self.hub.demo},
            "model": ev.get("model"),
        }

    def _why(self, wid: str) -> str:
        """A plain-words line from the numbers we have. (The model-based "why" is Phase 5, ML session.)"""
        hr = self.hub.pipelines[wid].signals["hr"]
        recent = [h for h in hr[-11:] if h == h]          # drop NaN
        if len(recent) < 2:
            return ""
        change = recent[-1] - recent[0]
        trend = f"up {change:.0f}" if change >= 5 else f"down {-change:.0f}" if change <= -5 else "steady"
        wbgt = self.hub.pipelines[wid].context.get("wbgt")
        return f"Heart rate {recent[-1]:.0f} bpm, {trend} over 10 min" + (f", WBGT {wbgt:.0f} °C." if wbgt else ".")

    def minutes_to_danger(self, wid: str) -> int | None:
        p = self.hub.pipelines[wid]
        if p.heat.level == "red":
            return 0
        last = p.estimates[-1] if p.estimates else None
        if not last or not last.get("forecast"):
            return None
        _, danger = self.thresholds()
        if last["core_temp"] is not None and last["core_temp"] >= danger:
            return 0
        for m, v in zip(last["forecast"]["minutes"], last["forecast"]["p90"]):
            if v is not None and v >= danger:
                return m
        return None

    def hr_missing(self, wid: str) -> bool:
        """No heart rate for HR_MISSING_S of this worker's own data time (other readings still coming)."""
        p = self.hub.pipelines[wid]
        if p.last_t is None:
            return False
        since = p.last_reading.get("hr_at", p.t0)
        return since is not None and p.last_t - since >= HR_MISSING_S

    def worker_status(self, wid: str) -> dict:
        p, info = self.hub.pipelines[wid], self.hub.workers[wid]
        status = STATUS[p.heat.level]
        mins = self.minutes_to_danger(wid)
        incident = self.incidents.get(wid)
        if incident and incident["reason"] == "sos":
            reason = "SOS pressed"
        elif p.mandown.state != "ok":
            reason = "Man-down check" if p.mandown.state == "checking" else "Man-down confirmed"
        elif status == "red":
            reason = "In heat danger now"
        elif self.hr_missing(wid):
            # Without heart rate the Kalman only predicts: don't show a confident "Normal".
            reason = "Heart rate missing"
        elif mins is not None:
            reason = f"Forecast to reach danger in ~{mins} min"
        elif status == "yellow":
            reason = "Heat warning"
        else:
            reason = "Normal"
        now = self.hub.now(wid)
        on_break, break_at = schedule_status(self.hub.schedule, wid, now)
        return {
            "worker_id": wid, "name": info["name"], "role": info["meta"].get("role", "Crew"),
            "site_id": info["site_id"], "status": status, "minutes_to_danger": mins, "status_reason": reason,
            "hazard": "SOS" if incident and incident["reason"] == "sos"
                      else None if p.router.current == "OFF" else p.router.current,
            "alert_acknowledged": p.heat.acknowledged if p.heat.level == "red" else None,
            "on_break": on_break, "break_at": break_at,       # from session B's schedule, if any
            "band_connected": p.last_t is not None and now - p.last_t < BAND_SEEN_S * max(1.0, self.speed()),
            "updated_at": p.last_t or now,
        }

    def _status_if_changed(self, wid: str) -> list[dict]:
        if wid not in self.hub.workers:
            return []
        st = self.worker_status(wid)
        key = {k: v for k, v in st.items() if k != "updated_at"}
        if self.last_status.get(wid) == key:
            return []
        self.last_status[wid] = key
        return [{"type": "worker_status", "worker": st}]

    def site(self, site_id: str) -> dict:
        s = self.hub.sites[site_id]
        return {"id": site_id, "name": s.get("name", site_id), "operator": s.get("operator", ""),
                "tech": s.get("tech", "gas"), "capacity_mw": s.get("capacity_mw", 0), "lat": s.get("lat"),
                "lon": s.get("lon"), "county": s.get("county", ""), "coastal": s.get("coastal", False)}

    def site_status(self, site_id: str) -> dict:
        """
        Worker-layer view of a site. Session C1 can push its full hazard list with
        hub.set_site_status() / POST /api/sites/{id}/status; we keep its hazards and
        only fill in the worker flags here.
        """
        pushed = self.hub.site_status.get(site_id, {})
        active = self.hub.hazards.get(site_id, {})
        hazards = pushed.get("hazards")
        if not hazards:
            hazards = []
            for kind in HAZARD_KINDS:
                hits = [ev for k, ev in active.items() if k == kind or (kind == "fire" and k in ("red_flag", "fire_weather"))]
                ev = hits[0] if hits else None
                hazards.append({
                    "kind": kind, "level": "high" if ev else "none",
                    "headline": f"{kind.capitalize()} alert" if ev else "None",
                    "detail": (ev or {}).get("details", {}).get("detail", "") if ev else "",
                    "source": ev["source"] if ev else "", "updated_at": ev["timestamp"] if ev else self.sim_time(),
                    "stop_work": bool(ev) and kind == "lightning",
                    "band_pattern": ev["pattern"] if ev else None,
                })
        on_site = [w for w, i in self.hub.workers.items() if i["site_id"] == site_id]
        flag = "none"
        if any(self.incidents.get(w, {}).get("reason") == "sos" for w in on_site):
            flag = "sos"
        elif any(self.hub.pipelines[w].mandown.state != "ok" for w in on_site):
            flag = "mandown"
        elif any(self.hub.pipelines[w].heat.level != "clear" for w in on_site):
            flag = "heat"
        return {"site_id": site_id, "hazards": hazards,
                "index_tomorrow": pushed.get("index_tomorrow", 0), "index_drivers": pushed.get("index_drivers", []),
                "worker_flag": flag, "workers_on_site": len(on_site), "updated_at": self.sim_time()}

    def demo_state(self) -> dict:
        warning, danger = self.thresholds()
        r = self.hub.replay
        return {
            "source": "replay" if r is not None else "live", "speed": r.speed if r is not None else 1.0,
            "demo_threshold": self.hub.demo, "thresholds": {"warning": warning, "danger": danger},
            "scenario": {"running": r is not None and not r.finished,
                         "step": r.plan.description if r is not None else None,
                         "started_at": r.start if r is not None else None},
            "mock": False,
        }

    def sim_time(self) -> float:
        r = self.hub.replay
        return r.clock.now() if r is not None else time.time()

    def speed(self) -> float:
        return self.hub.replay.speed if self.hub.replay is not None else 1.0

    # ------------------------------------------------------------------
    # Alerts, man-down, emergency
    # ------------------------------------------------------------------
    def _new_alert(self, kind, wid, site, at, message, source) -> dict:
        a = {"id": f"al-{next(self.ids)}", "kind": kind, "worker_id": wid, "site_id": site, "started_at": at,
             "message": message, "source": source, "acknowledged_at": None, "cleared_at": None}
        self.alerts.append(a)
        return a

    def _heat_alert(self, ev: dict) -> list[dict]:
        wid, t, out = ev["worker_id"], ev["timestamp"], []
        old = self.active_heat_alert.pop(wid, None)
        if old:
            old["cleared_at"] = t
            out.append({"type": "alert", "alert": old})
        if ev["level"] in ("yellow", "red"):
            name = self.hub.workers.get(wid, {}).get("name", wid)
            kind = "HEAT_YELLOW" if ev["level"] == "yellow" else "HEAT_RED"
            words = "Heat warning" if kind == "HEAT_YELLOW" else "Heat danger"
            source = "forecaster" if ev["rule"] == "forecast" else "Kalman estimate"
            a = self._new_alert(kind, wid, self.hub.workers.get(wid, {}).get("site_id"), t, f"{words}: {name}", source)
            self.active_heat_alert[wid] = a
            out.append({"type": "alert", "alert": a})
        return out

    def _hazard(self, ev: dict) -> list[dict]:
        key, t = (ev["site_id"], ev["kind"]), ev["timestamp"]
        if ev["on"] and key not in self.active_hazard_alert:
            a = self._new_alert(ev["pattern"], None, ev["site_id"], t,
                                f"{ev['kind'].replace('_', ' ').capitalize()} at {self.hub.sites.get(ev['site_id'], {}).get('name', ev['site_id'])}",
                                ev["source"])
            self.active_hazard_alert[key] = a
            return [{"type": "alert", "alert": a}]
        if not ev["on"] and key in self.active_hazard_alert:
            a = self.active_hazard_alert.pop(key)
            a["cleared_at"] = t
            return [{"type": "alert", "alert": a}]
        return []

    # One incident (man-down or SOS) per worker; the contract shows the newest active one.
    @property
    def mandown(self) -> dict | None:
        return max(self.incidents.values(), key=lambda m: m["check_started_at"], default=None)

    def _incident(self, wid: str, t: float, started: float, reason: str, stage: str, alert_kind: str,
                  source: str) -> dict:
        site = self.hub.sites.get(self.hub.workers[wid]["site_id"], {})
        p = self.hub.pipelines.get(wid)
        last = p.estimates[-1] if p and p.estimates else {}
        sos = reason == "sos"
        md = {
            "id": f"{'sos' if sos else 'md'}-{wid}-{int(t)}", "worker_id": wid,
            "worker_name": self.hub.workers[wid]["name"], "site_id": site.get("site_id"),
            "site_name": site.get("name"), "lat": site.get("lat"), "lon": site.get("lon"),
            "fell_at": started, "check_started_at": t,
            "confirm_at": t if sos else t + config.MANDOWN_ESCALATE_S,
            "confirmed_at": t if sos else None, "stage": stage, "reason": reason,
            "last_vitals": {"core_temp": last.get("core_temp"), "hr": p.last_reading.get("hr") if p else None,
                            "at": last.get("timestamp", t)},
        }
        name = md["worker_name"]
        words = f"SOS from {name}" if sos else f"Man-down check: {name}"
        md["_alert"] = self._new_alert(alert_kind, wid, site.get("site_id"), t, words, source)
        self.incidents[wid] = md
        return md

    def _close_incident(self, wid: str, t: float, stage: str) -> list[dict]:
        md = self.incidents.pop(wid, None)
        if md is None:
            return []
        md["stage"] = stage
        md["_alert"]["cleared_at"] = t
        out = [{"type": "alert", "alert": md["_alert"]}, self._mandown_msg(md)]
        nxt = self.mandown                                     # another worker's incident, if any
        return out + [{"type": "mandown", "mandown": self._public(nxt) if nxt else None}]

    def _mandown(self, ev: dict) -> list[dict]:
        kind, wid, t = ev["type"], ev["worker_id"], ev["timestamp"]
        if kind == "mandown_check":
            reason = MANDOWN_REASON.get(ev["reason"], "demo")
            source = {"fall": "tilt switch", "no_response_to_red": "unacknowledged red alert"}.get(reason, "demo button")
            md = self._incident(wid, t, ev["started_at"], reason, "checking", "MANDOWN", source)
            return [{"type": "alert", "alert": md["_alert"]}, self._mandown_msg(md)]
        md = self.incidents.get(wid)
        if md is None or md["reason"] == "sos":
            return []
        if kind == "mandown_confirmed":
            md.update(stage="confirmed", confirmed_at=t)
            md["last_vitals"] = {"core_temp": ev.get("core_temp_c"), "hr": ev.get("hr_bpm"), "at": t}
            return [self._mandown_msg(md)]
        if kind in ("mandown_cancelled", "mandown_resolved"):
            return self._close_incident(wid, t, "cancelled" if kind == "mandown_cancelled" else "resolved")
        return []

    def _sos(self, ev: dict) -> list[dict]:
        """BTN:SOS (or the phone button): an incident that starts confirmed. Ignored if one is active."""
        wid, t = ev["worker_id"], ev["timestamp"]
        escalated = wid not in self.incidents
        sos = {k: ev.get(k) for k in ("worker_id", "worker_name", "site_id", "site_name", "lat", "lon", "pressed_at")}
        sos.update(source=ev.get("source", "band"), escalated=escalated)
        out = [{"type": "sos", "sos": sos}]
        if escalated:
            md = self._incident(wid, t, t, "sos", "confirmed", "SOS", f"SOS ({sos['source']})")
            out += [{"type": "alert", "alert": md["_alert"]}, self._mandown_msg(md)]
        return out

    @staticmethod
    def _public(md: dict) -> dict:
        return {k: v for k, v in md.items() if not k.startswith("_")}

    def _mandown_msg(self, md: dict) -> dict:
        return {"type": "mandown", "mandown": self._public(md)}

    def _emergency(self, ev: dict) -> list[dict]:
        stage, status = ev["stage"], ev["status"]
        if stage == "notify":
            mapped = "notifying"
        elif stage == "cancel_window":
            mapped = "cancel_window"
        elif stage == "emergency_call":
            mapped = "connected" if status in ("connected", "completed") else "calling"
        else:
            mapped = "cancelled" if status == "cancelled" else "done"
        call = self.emergency if self.emergency and self.emergency["id"] == ev["incident_id"] else None
        if call is None:
            call = self.emergency = {
                "id": ev["incident_id"], "mandown_id": (self.mandown or {}).get("id", ""),
                "worker_id": ev["worker_id"], "stage": mapped, "started_at": ev["timestamp"],
                "cancel_deadline": None, "demo": True, "target_label": "Demo: would call 911",
                "steps": [], "transcript": [], "outcome": None,
            }
        call["stage"] = mapped
        call["demo"] = ev.get("mode") != "live" or bool(ev.get("demo_label"))
        if ev.get("demo_label"):
            call["target_label"] = ev["demo_label"]
        if ev.get("cancel_deadline"):
            call["cancel_deadline"] = ev["cancel_deadline"]
        call["steps"].append({"at": ev["timestamp"], "stage": mapped, "text": ev.get("message", "")})
        if mapped in ("done", "cancelled"):
            call["outcome"] = ev.get("message") or mapped
        return [{"type": "emergency", "emergency": call}]

    def _log_text(self, ev: dict) -> str | None:
        kind = ev["type"]
        name = self.hub.workers.get(ev.get("worker_id"), {}).get("name", ev.get("worker_id"))
        if kind == "heat_alert":
            return f"{name}: heat {ev['level']} ({ev['rule']} rule)"
        if kind == "heat_ack":
            return f"{name} acknowledged the red alert"
        if kind == "hazard":
            return f"{ev['kind'].replace('_', ' ').capitalize()} {'ON' if ev['on'] else 'off'} at {ev['site_id']}"
        if kind == "mandown_check":
            return f"{name}: man-down check ({ev['reason']})"
        if kind == "mandown_confirmed":
            return f"{name}: MAN-DOWN CONFIRMED"
        if kind in ("mandown_cancelled", "mandown_resolved"):
            return f"{name}: man-down {kind.split('_')[1]} ({ev['by']})"
        if kind == "sos":
            return f"{name}: SOS pressed"
        if kind == "hr_link" and ev["state"] in ("connected", "stale", "lost"):
            return f"{name}: Bluetooth heart rate {ev['state']}" + (f" ({ev['device']})" if ev.get("device") else "")
        if kind == "demo":
            return f"Demo thresholds {'ON' if ev['demo'] else 'off'}"
        return None

    # ------------------------------------------------------------------
    # Snapshot + subscribers (with the privacy filter)
    # ------------------------------------------------------------------
    @staticmethod
    def can_see(f: dict, msg: dict) -> bool:
        if msg["type"] in ("vitals", "estimate", "offset_update"):
            # Body data (raw vitals, core temp, a worker's personal offset): demo view, or the worker's own.
            wid = msg["worker_id"] if msg["type"] == "offset_update" else msg[msg["type"]]["worker_id"]
            return f["view"] == "demo" or (f["view"] == "worker" and f.get("worker_id") == wid)
        if msg["type"] == "band_command":
            return f["view"] != "crew"
        return True

    def snapshot(self, f: dict) -> dict:
        history = {}
        for wid in self.hub.pipelines:
            if f["view"] == "demo" or (f["view"] == "worker" and f.get("worker_id") == wid):
                history[wid] = {"vitals": list(self.vitals.get(wid, []))[-720:],
                                "estimates": list(self.estimates.get(wid, []))[-240:]}
        return {
            "type": "snapshot", "view": f["view"], "sim_time": self.sim_time(),
            "workers": [self.worker_status(w) for w in self.hub.pipelines],
            "sites": [self.site_status(s) for s in self.hub.sites],
            "alerts": list(self.alerts),
            "mandown": None if self.mandown is None else self._public(self.mandown),
            "emergency": self.emergency, "demo": self.demo_state(), "reports": self.reports,
            "log": list(self.log)[-100:], "history": history,
            "band": list(self.band) if f["view"] != "crew" else [],
            # Personal offsets (body data): same rule as history, demo view or the worker's own.
            "offsets": {wid: self.offset_state(wid) for wid in history},
        }

    def offset_state(self, wid: str) -> dict:
        """A worker's personal offset in the same flat shape as the offset_update event."""
        p = self.hub.pipelines[wid]
        return {"type": "offset_update", "worker_id": wid, "timestamp": p.last_t, "accepted": True,
                **p.offset_status()}

    @staticmethod
    def for_view(f: dict, msg: dict) -> dict:
        """The version of a message this view may see (crew: no body numbers in planner_change)."""
        if msg["type"] == "planner_change" and f["view"] == "crew":
            return {k: v for k, v in msg.items() if k not in PLANNER_BODY_FIELDS}
        return msg

    def broadcast(self, msg: dict) -> None:
        for q, f in list(self.subscribers.items()):
            if self.can_see(f, msg):
                try:
                    q.put_nowait(self.for_view(f, msg))
                except asyncio.QueueFull:
                    pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
router = APIRouter()


def _a(request) -> FrontendAdapter:
    return request.app.state.frontend


def _view(view: str, worker_id: str | None) -> dict:
    if view not in ("crew", "worker", "demo"):
        raise HTTPException(400, "view must be crew, worker or demo")
    return {"view": view, "worker_id": worker_id}


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket, view: str = "crew", worker_id: str | None = None):
    from backend.app.main import _clean
    await websocket.accept()
    a = websocket.app.state.frontend
    f = {"view": view if view in ("crew", "worker", "demo") else "crew", "worker_id": worker_id}
    q = asyncio.Queue(maxsize=5000)
    a.subscribers[q] = f
    try:
        await websocket.send_json(_clean(a.snapshot(f)))
        while True:
            await websocket.send_json(_clean(await q.get()))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        a.subscribers.pop(q, None)


@router.get("/api/state")
async def state(request: Request, view: str = "crew", worker_id: str | None = None):
    from backend.app.main import _clean
    return _clean(_a(request).snapshot(_view(view, worker_id)))


@router.get("/api/workers")
async def workers(request: Request):
    a = _a(request)
    return [a.worker_status(w) for w in a.hub.pipelines]


@router.get("/api/workers/{worker_id}/history")
async def history(request: Request, worker_id: str, view: str = "crew", minutes: float = 60):
    from backend.app.main import _clean
    a, f = _a(request), _view(view, None)
    if worker_id not in a.hub.pipelines:
        raise HTTPException(404, "unknown worker")
    if f["view"] == "crew":
        raise HTTPException(403, "crew view cannot read raw biometrics")
    since = (a.hub.pipelines[worker_id].last_t or a.hub.now(worker_id)) - minutes * 60   # last N minutes of data
    return _clean({"vitals": [v for v in a.vitals.get(worker_id, []) if v["timestamp"] >= since],
                   "estimates": [e for e in a.estimates.get(worker_id, []) if e["timestamp"] >= since]})


@router.get("/api/sites")
async def sites(request: Request):
    a = _a(request)
    return [a.site(s) for s in a.hub.sites]


@router.get("/api/schedule")
async def schedule(request: Request):
    """
    Fallback only: when session B's planner is merged, its router serves /api/schedule (mounted first).
    Without it: the latest Schedule-shaped plan posted to /api/plans.
    """
    if _a(request).hub.schedule:
        return _a(request).hub.schedule
    for ev in reversed(_a(request).hub.plans):
        if isinstance(ev["plan"], dict) and "rows" in ev["plan"]:
            return ev["plan"]
    raise HTTPException(404, "no schedule yet (session B posts one to /api/plans)")


@router.get("/api/briefing")
async def get_briefing(request: Request):
    b = _a(request).briefing
    if b is None:
        raise HTTPException(404, "no briefing yet (session B posts one to /api/briefing)")
    return b


@router.post("/api/briefing")
async def post_briefing(request: Request, body: dict):
    _a(request).briefing = body
    return {"ok": True}


@router.get("/api/reports")
async def get_reports(request: Request):
    return _a(request).reports


@router.post("/api/reports")
async def add_report(request: Request, body: dict):
    """Crew photo report (snake, standing water, downed line ...)."""
    a = _a(request)
    photo = body.get("photo")
    if photo is not None and (not isinstance(photo, str) or len(photo) > MAX_PHOTO_CHARS):
        raise HTTPException(413, "photo must be a JPEG data URL under about 1.5 MB")
    site_id = body.get("site_id") or a.hub.default_site_id
    site = a.hub.sites.get(site_id, {})
    r = {"id": f"rp-{next(a.ids)}", "site_id": site_id, "worker_id": body.get("worker_id"),
         "kind": body.get("kind", "other"), "note": str(body.get("note", ""))[:500],
         "lat": body.get("lat", site.get("lat")), "lon": body.get("lon", site.get("lon")),
         "photo": body.get("photo"), "at": a.sim_time()}
    a.reports.append(r)
    if a.hub.db:
        a.hub.db.add_report(r)
    a.broadcast({"type": "report", "report": r})
    return {"ok": True, "report": r}


@router.post("/api/workers/{worker_id}/button")
async def button(request: Request, worker_id: str):
    a = _a(request)
    if worker_id not in a.hub.pipelines:
        raise HTTPException(404, "unknown worker")
    a.hub.press_button(worker_id)
    return {"ok": True}


@router.post("/api/workers/{worker_id}/sos")
async def sos(request: Request, worker_id: str):
    """The phone view's hold-3-s SOS button. Same as the band's BTN:SOS."""
    a = _a(request)
    if worker_id not in a.hub.pipelines:
        raise HTTPException(404, "unknown worker")
    active = worker_id in a.incidents
    a.hub.press_sos(worker_id, source="phone")
    return {"ok": True, "action": "ignored_active_incident" if active else "escalated"}


@router.post("/api/alerts/{alert_id}/ack")
async def ack_alert(request: Request, alert_id: str):
    """Heat alerts are acknowledged like the band button would; others are just marked seen."""
    a = _a(request)
    al = next((x for x in a.alerts if x["id"] == alert_id), None)
    if al is None:
        raise HTTPException(404, "unknown alert")
    if al["kind"] == "HEAT_RED" and al["worker_id"] in a.hub.pipelines and al["cleared_at"] is None:
        a.hub.press_button(al["worker_id"])
    elif al["acknowledged_at"] is None:
        al["acknowledged_at"] = a.sim_time()
        a.broadcast({"type": "alert", "alert": al})
    return {"ok": True}


@router.post("/api/mandown/{mandown_id}/resolve")
async def resolve(request: Request, mandown_id: str):
    """Crew lead closes a man-down or SOS incident. Accepts the incident id or a worker id."""
    a = _a(request)
    md = next((m for m in a.incidents.values() if m["id"] == mandown_id), None)
    wid = md["worker_id"] if md else mandown_id
    if wid not in a.hub.pipelines:
        raise HTTPException(404, "unknown man-down")
    if md and md["reason"] == "sos":
        for msg in a._close_incident(wid, a.hub.now(wid), "resolved"):   # SOS never touched the detector
            a.broadcast(msg)
    else:
        a.hub.resolve_mandown(wid)
    return {"ok": True}


@router.post("/api/emergency/{call_id}/cancel")
async def cancel_call(request: Request, call_id: str):
    a = _a(request)
    manager = getattr(request.app.state, "emergency", None)
    if manager is None:
        raise HTTPException(409, "emergency escalation (session D) is not merged in")
    wid = a.emergency["worker_id"] if a.emergency and a.emergency["id"] == call_id else None
    return {"ok": manager.cancel(incident_id=call_id, worker_id=wid)}


@router.post("/api/demo/test-alert")
async def test_alert(request: Request, body: dict):
    pattern = str(body.get("kind", "LIGHTNING")).upper()
    kind = {"LIGHTNING": "lightning", "FLOOD_FIRE": "flood"}.get(pattern)
    if kind is None:
        raise HTTPException(400, "kind must be LIGHTNING or FLOOD_FIRE")
    bridge = getattr(request.app.state, "sites", None)
    if bridge is not None:
        bridge.test_alert(pattern, body.get("site_id"))     # C1's rules + countdown pick it up on refresh
    _a(request).hub.set_site_hazard(body.get("site_id"), kind, True, source="demo button")   # band reacts now
    return {"ok": True}


@router.post("/api/demo/clear-hazards")
async def clear_hazards(request: Request):
    hub = _a(request).hub
    bridge = getattr(request.app.state, "sites", None)
    if bridge is not None:
        bridge.clear_test_hazards()
    for site, kinds in list(hub.hazards.items()):
        for kind in list(kinds):
            hub.set_site_hazard(site, kind, False, source="demo button")
    return {"ok": True}


@router.post("/api/demo/replay")
async def demo_replay(request: Request, body: dict):
    """{on?: bool, speed?: number}. Turning replay on starts the demo shift."""
    from backend.app.main import SETTINGS, make_replay
    a = _a(request)
    speed = body.get("speed")
    if speed is not None and not (0 < float(speed) <= 600):
        raise HTTPException(400, "speed must be between 0 and 600")
    if body.get("on") is True and a.hub.replay is None:
        a.hub.start_replay(make_replay(a.hub, SETTINGS, speed=float(speed) if speed else None))
    elif body.get("on") is False:
        a.hub.stop_replay()
    elif speed is not None and a.hub.replay is not None:
        a.hub.set_speed(float(speed))
    return a.demo_state()


@router.post("/api/demo/scenario")
async def demo_scenario(request: Request, body: dict):
    """start = (re)start the scripted demo shift from the beginning; stop = stop it."""
    from backend.app.main import SETTINGS, make_replay
    a = _a(request)
    if body.get("action") == "start":
        a.hub.start_replay(make_replay(a.hub, SETTINGS))
    else:
        a.hub.stop_replay()
    return a.demo_state()


@router.post("/api/demo/mandown")
async def demo_mandown(request: Request, body: dict):
    a = _a(request)
    wid = body.get("worker_id") or next(iter(a.hub.pipelines), None)
    if wid not in a.hub.pipelines:
        raise HTTPException(404, "unknown worker")
    a.hub.fake_fall(wid)
    return {"ok": True}
