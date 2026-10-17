"""
One worker's live pipeline.

    Reading (laptop timestamp) -> 1-minute averages -> Kalman (ECTemp)
        -> forecaster v1 (if loaded) -> heat alert -> band router -> events

Everything runs on the READINGS' timestamps, never on the wall clock. A
replay at 10x just sends readings with timestamps 10x faster, and every
timer here (minutes, man-down, red-alert timeout) speeds up with it.

handle(event) and tick(t) both return a list of plain dict events. The hub
sends those to the dashboard, the band, and the database.
"""

import logging
import math
from collections import deque

import numpy as np

from backend import config
from backend.alerts.heat import RED, YELLOW, HeatAlerter, HeatInputs
from backend.alerts.mandown import ManDownDetector
from backend.alerts.router import BandRouter
from backend.app.schemas import ButtonPress, Reading, SosPress
from backend.models.kalman import ECTempKalman

log = logging.getLogger(__name__)

AVERAGED = ("hr", "skin_temp", "activity")      # per-minute averages of these reading fields
CONTEXT = ("wbgt", "workload")                  # per-minute values set from outside (weather, plan)
HISTORY_KEEP = 600                              # estimates kept in memory for the dashboard (10 h)


def _r(x, nd=3):
    """Round for JSON. NaN/None -> None."""
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else round(x, nd)


class WorkerPipeline:
    def __init__(self, worker_id: str, predictor=None, demo: bool = False,
                 info: dict | None = None, site: dict | None = None):
        """
        predictor: a ForecasterPredictor, or None for Kalman-only.
        info: {"name": ..., "site_id": ...}   site: {"site_id", "name", "lat", "lon"}
        """
        self.worker_id = worker_id
        self.predictor = predictor
        self.info = info or {"name": worker_id}
        self.site = site or {}

        # Minute bucketing: minute k covers [t0 + 60k, t0 + 60(k+1)).
        self.t0: float | None = None
        self.k = 0                                       # the minute being filled now
        self.bucket = {name: [] for name in AVERAGED}
        self.empty_run = 0                               # minutes in a row with no readings at all
        self.n_minutes = 0                               # minutes closed so far

        # Per-minute history (what the forecaster reads), one list per signal.
        self.signals = {name: [] for name in AVERAGED + CONTEXT}
        self.context = {name: None for name in CONTEXT}  # current weather / planned workload
        # Known future (next 30 min): forecast WBGT and planned workload. The forecaster's
        # decoder reads these; without them its 90th percentile runs noticeably higher.
        self.future = {name: None for name in CONTEXT}

        self.kalman = ECTempKalman()
        self.heat = HeatAlerter(demo)
        self.router = BandRouter(worker_id)
        self.mandown = ManDownDetector()
        self.last_inputs: HeatInputs | None = None
        self.last_t: float | None = None                 # latest timestamp we've seen
        self.last_reading: dict = {}                     # latest value of each reading field + when
        self.estimates: deque = deque(maxlen=HISTORY_KEEP)

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    def handle(self, event: Reading | ButtonPress | SosPress) -> list[dict]:
        t = event.timestamp
        events = self._advance(t)
        if isinstance(event, SosPress):
            # Held the button 3 s: session D escalates right away. A deliberate press is also
            # movement, but it must NOT cancel anything, so it skips the man-down detector.
            self.mandown.last_movement_at = t
            events.append({"type": "sos", "timestamp": t, "pressed_at": t, **self.mandown_payload()})
        elif isinstance(event, ButtonPress):
            events += self.heat.acknowledge(t)
            events += self._mandown(self.mandown.on_button(t))
        else:
            for name in AVERAGED:
                value = getattr(event, name)
                if value is not None:
                    self.bucket[name].append(float(value))
            for name in ("hr", "skin_temp", "tilt", "activity", "accel"):
                value = getattr(event, name)
                if value is not None:
                    self.last_reading[name] = value
                    self.last_reading[f"{name}_at"] = t
            events += self._mandown(self.mandown.on_reading(t, event.tilt, event.activity))
        return self._stamp(events)

    def tick(self, t: float) -> list[dict]:
        """Called about once a second with the source's clock. Closes quiet minutes and checks timers."""
        # The shift starts at the first reading, not at a tick. Timers still run.
        events = self._advance(t) if self.t0 is not None else []   # already reacted to inside
        events += self._react(self.heat.tick(t), t)
        events += self._mandown(self.mandown.tick(t))
        return self._stamp(events)

    def set_context(self, wbgt: float | None = None, workload: float | None = None,
                    future_wbgt: list | None = None, future_workload: list | None = None) -> None:
        """
        Weather (WBGT, °C) and planned workload (W/m²) from the hazard feed / planner.
        future_*: values for the next minutes (index 0 = next minute, up to 30).
        """
        if wbgt is not None:
            self.context["wbgt"] = wbgt
        if workload is not None:
            self.context["workload"] = workload
        if future_wbgt is not None:
            self.future["wbgt"] = future_wbgt
        if future_workload is not None:
            self.future["workload"] = future_workload

    def set_demo(self, on: bool) -> list[dict]:
        """Switch demo thresholds and re-check right away (no waiting for the next minute)."""
        self.heat.set_demo(on)
        if self.last_inputs is None or self.last_t is None:
            return []
        return self._stamp(self._react(self.heat.update(self.last_t, self.last_inputs), self.last_t))

    def set_hazard(self, pattern: str, on: bool, t: float) -> list[dict]:
        """Site hazard (LIGHTNING or FLOOD_FIRE) on/off for this worker's band."""
        return self._stamp(self.router.set(pattern, on, t))

    def trigger_mandown(self, t: float, reason: str) -> list[dict]:
        """Start a man-down check from outside (e.g. demo button)."""
        return self._stamp(self._mandown(self.mandown.trigger(t, reason)))

    def resolve_mandown(self, t: float, by: str = "crew_lead") -> list[dict]:
        return self._stamp(self._mandown(self.mandown.resolve(t, by)))

    # ------------------------------------------------------------------
    # Minutes
    # ------------------------------------------------------------------
    def _advance(self, t: float) -> list[dict]:
        """Close every minute that ended before time t."""
        if self.t0 is None:
            self.t0 = t
        self.last_t = t if self.last_t is None else max(self.last_t, t)
        events = []
        target = math.floor((t - self.t0) / 60)          # the minute time t falls in
        while self.k < target:
            events += self._close_minute()
            self.k += 1
            if self.empty_run > config.MAX_GAP_MIN:
                self.k = target                          # long silence: jump ahead instead of looping
        return events

    def _close_minute(self) -> list[dict]:
        has_data = any(self.bucket[name] for name in AVERAGED)
        self.empty_run = 0 if has_data else self.empty_run + 1
        if self.empty_run > config.MAX_GAP_MIN:
            return []      # long silence (band off): stop adding empty minutes until data comes back

        for name in AVERAGED:
            values = self.bucket[name]
            self.signals[name].append(float(np.mean(values)) if values else np.nan)
            self.bucket[name] = []
        for name in CONTEXT:
            value = self.context[name]
            self.signals[name].append(np.nan if value is None else float(value))
        minute_end = self.t0 + 60 * (self.k + 1)
        estimate = self._estimate(minute_end)
        self.n_minutes += 1
        self.estimates.append(estimate)
        return [estimate] + self._react(self.heat.update(minute_end, self.last_inputs), minute_end)

    def _estimate(self, t: float) -> dict:
        """Run the Kalman (always) and the forecaster (if we have one) on the history so far."""
        hr = self.signals["hr"][-1]
        ks = self.kalman.step(None if np.isnan(hr) else hr)
        core, core_sd, curve, model = ks.ct, math.sqrt(ks.v), None, "kalman"

        if self.predictor is not None:
            try:
                arrays = self._model_arrays()
                kal, now_delta, now_var, fc = self.predictor.predict_signals(arrays)
                i = self.n_minutes                       # this minute's row (rows after it = known future)
                core = float(kal[i] + now_delta[i])
                core_sd = float(math.sqrt(now_var[i]))
                curve = kal[i] + fc[i]                   # (30, 3): °C at +1..+30 min, 10/50/90th pct
                model = self.predictor.name
            except Exception as e:                       # never let a model bug stop the Kalman alerts
                log.warning("forecaster failed for %s, using Kalman only: %s", self.worker_id, e)
                curve = None

        if curve is not None:
            p90 = config.FORECAST_QUANTILES.index(config.HEAT_ALERT_QUANTILE)
            self.last_inputs = HeatInputs(core, float(curve[config.HEAT_DANGER_HORIZON_MIN - 1, p90]),
                                          float(curve[config.HEAT_WARNING_HORIZON_MIN - 1, p90]))
        else:
            self.last_inputs = HeatInputs(core)

        forecast = None
        if curve is not None:
            forecast = {
                "minutes": list(range(1, curve.shape[0] + 1)),
                "p10": [_r(v) for v in curve[:, 0]],
                "p50": [_r(v) for v in curve[:, 1]],
                "p90": [_r(v) for v in curve[:, 2]],
            }
        sig = lambda name: _r(self.signals[name][-1], 2)
        return {
            "type": "estimate", "timestamp": t, "minute": self.k,
            "hr": sig("hr"), "skin_temp": sig("skin_temp"), "activity": sig("activity"), "wbgt": sig("wbgt"),
            "kalman": _r(ks.ct), "kalman_sd": _r(math.sqrt(ks.v), 4),
            "core_temp": _r(core), "core_temp_sd": _r(core_sd, 4),
            "forecast": forecast, "model": model,
        }

    def _model_arrays(self) -> dict[str, np.ndarray]:
        """
        History so far, plus 30 rows of known future (only WBGT / workload filled in).
        The model is causal, so the extra rows can't change anything at this minute;
        they only feed the forecast decoder, exactly as in training.
        """
        horizon = max(config.FORECAST_HORIZONS_MIN)
        arrays = {}
        for name, values in self.signals.items():
            future = np.full(horizon, np.nan)
            known = self.future.get(name)
            if known is not None:
                known = np.array([np.nan if v is None else v for v in known[:horizon]], dtype=float)
                future[:len(known)] = known
            arrays[name] = np.concatenate([np.array(values, dtype=float), future])
        return arrays

    # ------------------------------------------------------------------
    # Reactions: heat level / man-down -> band router
    # ------------------------------------------------------------------
    def _react(self, events: list[dict], t: float) -> list[dict]:
        out = list(events)
        for ev in events:
            if ev["type"] == "heat_alert":
                out += self.router.set_many({"HEAT_YELLOW": ev["level"] == YELLOW,
                                             "HEAT_RED": ev["level"] == RED}, t)
            elif ev["type"] == "red_unacknowledged":
                # Spec: a red alert with no button press within a minute also triggers a man-down check.
                out += self._mandown(self.mandown.trigger(t, "heat_red_unacknowledged"))
        if self.estimates:
            self.estimates[-1]["heat_level"] = self.heat.level
        return out

    def _mandown(self, events: list[dict]) -> list[dict]:
        """Man-down events: drive the band, and fill in the confirmed payload."""
        out = []
        for ev in events:
            if ev["type"] == "mandown_check":
                out += [ev] + self.router.set("MANDOWN", True, ev["timestamp"])
            elif ev["type"] in ("mandown_cancelled", "mandown_resolved"):
                out += [ev] + self.router.set("MANDOWN", False, ev["timestamp"])
            elif ev["type"] == "mandown_confirmed":
                payload = self.mandown_payload()
                trigger = "heat_red_unacked" if ev["reason"] == "heat_red_unacknowledged" else "fall"
                out.append({**ev, **payload, "trigger": trigger, "confirmed_at": ev["timestamp"]})
            else:
                out.append(ev)
        return out

    def mandown_payload(self) -> dict:
        """
        Who, where, and last vitals: what session D's voice agent reads out.
        Flat fields (lat, lon, core_temp_c, hr_bpm, skin_temp_c) match D's
        MandownConfirmed / SosPressed models; the nested ones are for the dashboard.
        """
        last = self.estimates[-1] if self.estimates else {}
        r = self.last_reading
        return {
            "worker_name": self.info.get("name", self.worker_id),
            "site_id": self.site.get("site_id"),
            "site_name": self.site.get("name"),
            "lat": self.site.get("lat"), "lon": self.site.get("lon"),
            "core_temp_c": last.get("core_temp"),
            "hr_bpm": _r(r.get("hr"), 1),
            "skin_temp_c": _r(r.get("skin_temp"), 2),
            "location": {"lat": self.site.get("lat"), "lon": self.site.get("lon")},
            "last_vitals": {
                "core_temp": last.get("core_temp"),
                "core_temp_sd": last.get("core_temp_sd"),
                "hr": _r(r.get("hr"), 1),
                "skin_temp": _r(r.get("skin_temp"), 2),
                "heat_level": self.heat.level,
                "estimate_at": last.get("timestamp"),
                "hr_at": r.get("hr_at"),
            },
        }

    def status(self) -> dict:
        """Current snapshot for the dashboard."""
        return {
            "worker_id": self.worker_id, "name": self.info.get("name", self.worker_id),
            "site_id": self.site.get("site_id"), "heat_level": self.heat.level,
            "acknowledged": self.heat.acknowledged, "band_pattern": self.router.current,
            "mandown": self.mandown.state, "mandown_reason": self.mandown.reason, "demo": self.heat.demo,
            "model": self.estimates[-1]["model"] if self.estimates else None,
            "latest": self.estimates[-1] if self.estimates else None,
            "last_reading": {k: v for k, v in self.last_reading.items() if not k.endswith("_at")},
        }

    def _stamp(self, events: list[dict]) -> list[dict]:
        for ev in events:
            ev.setdefault("worker_id", self.worker_id)
        return events
