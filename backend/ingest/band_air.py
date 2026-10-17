"""
The band's air sensor (BME280/BMP280): AIRT (°C), HUM (%), BARO (hPa), once a second.

Every minute we keep one summary per worker: average air temperature, humidity and pressure.
This is weather at the worker, not body data, so it may be shown in the worker and demo views.

Local WBGT correction (for that worker's forecaster input only)
    The site's WBGT comes from the weather feed (one number for the whole site). A worker in a
    hot spot (next to a transformer, on a roof) or in shade has different air. When the band's
    air reading is SANE we nudge that worker's WBGT input by how much the local air differs:

        delta = 0.567 * (T_band - T_site) + 0.393 * (e_band - e_site)

    That's the change in the Australian Bureau of Meteorology's simple shade WBGT
    (WBGT ≈ 0.567 T + 0.393 e + 3.94, e = water vapor pressure in hPa) between the two airs.
    It ignores sun and wind on purpose: the site WBGT already has those, we only correct the air.

    Assumptions (also in docs/sessions/band-sensors.md):
      - the air sensor faces OUT on the strap, in open air, not touching skin, away from the Feather
      - the site weather has an air temperature and humidity to compare with (C1 heat details,
        or POST /api/sites/{id}/weather with temp_c + rh_pct); without them there's no correction
    "Sane" minute: enough readings, steady, inside normal ranges, not suspiciously close to skin
    temperature (sensor touching the wrist), and not wildly different from the site (sensor in
    direct sun or under a sleeve). Otherwise the correction is 0.
    The correction is capped (WBGT_LOCAL_MAX_UP_C / WBGT_LOCAL_MAX_DOWN_C) and is the median of
    the last 5 sane minutes, so one odd minute can't swing it. It only changes the forecaster's
    WBGT input. Alert thresholds never change.
"""

import math
from collections import deque

import numpy as np

from backend import config

KEEP_MINUTES = 5                  # correction = median over the last 5 sane minutes


def vapor_pressure_hpa(temp_c: float, rh_pct: float) -> float:
    """Water vapor pressure in the air (hPa), from temperature and relative humidity (Magnus formula)."""
    return rh_pct / 100.0 * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))


def wbgt_delta(band_t: float, band_rh: float, site_t: float, site_rh: float) -> float:
    """How much higher (°C) the shade WBGT is at the band than at the site's weather station."""
    return 0.567 * (band_t - site_t) + 0.393 * (vapor_pressure_hpa(band_t, band_rh)
                                                 - vapor_pressure_hpa(site_t, site_rh))


class AirTracker:
    """Per-minute air summaries for one worker, plus the capped local WBGT correction."""

    def __init__(self):
        self.temp: list[float] = []
        self.hum: list[float] = []
        self.pres: list[float] = []
        self.deltas: deque = deque(maxlen=KEEP_MINUTES)
        self.latest: dict | None = None

    def add(self, air_temp: float | None, humidity: float | None, pressure: float | None) -> None:
        if air_temp is not None:
            self.temp.append(air_temp)
        if humidity is not None:
            self.hum.append(humidity)
        if pressure is not None:
            self.pres.append(pressure)

    def close_minute(self, t: float, skin_temp: float | None = None,
                     site_air: tuple[float, float] | None = None) -> dict | None:
        """
        Summarize the minute that just ended. site_air = (temp °C, RH %) from the site weather.
        Returns None if the band sent no air readings this minute.
        """
        temp, hum, pres = self.temp, self.hum, self.pres
        self.temp, self.hum, self.pres = [], [], []
        if not (temp or hum or pres):
            return None
        out = {
            "air_temp_c": round(float(np.mean(temp)), 2) if temp else None,
            "humidity_pct": round(float(np.mean(hum)), 1) if hum else None,
            "pressure_hpa": round(float(np.mean(pres)), 2) if pres else None,
            "n_air": len(temp),
        }
        reason = self._insane(temp, hum, skin_temp, site_air)
        out["air_sane"] = reason is None
        out["air_note"] = reason or "ok"
        if reason is None:
            delta = wbgt_delta(out["air_temp_c"], out["humidity_pct"], site_air[0], site_air[1])
            self.deltas.append(delta)
        elif reason not in ("no site weather to compare",):
            self.deltas.clear()          # sensor looks wrong: stop correcting until it looks right again
        corr = None
        if self.deltas and reason is None:
            corr = float(np.median(self.deltas))
            corr = max(-config.WBGT_LOCAL_MAX_DOWN_C, min(config.WBGT_LOCAL_MAX_UP_C, corr))
            corr = round(corr, 2)
        out["wbgt_correction_c"] = corr
        out["site_air_temp_c"] = site_air[0] if site_air else None
        out["site_humidity_pct"] = site_air[1] if site_air else None
        self.latest = {"timestamp": t, **out}
        return out

    @staticmethod
    def _insane(temp, hum, skin_temp, site_air) -> str | None:
        """Why this minute's air reading can't be used for the WBGT correction (None = usable)."""
        if len(temp) < config.BAND_AIR_MIN_SAMPLES or len(hum) < config.BAND_AIR_MIN_SAMPLES:
            return "too few readings (BMP280 has no humidity)" if temp and not hum else "too few readings"
        t, h = float(np.mean(temp)), float(np.mean(hum))
        if np.std(temp) > config.BAND_AIR_MAX_STD_C:
            return "air temperature jumping around"
        if not (0.0 <= t <= 50.0) or not (5.0 <= h <= 100.0):
            return "outside normal ranges"
        if skin_temp is not None and t > 30.0 and abs(t - skin_temp) < config.BAND_AIR_SKIN_GAP_C:
            return "reads like skin (sensor touching the wrist?)"
        if site_air is None or site_air[0] is None or site_air[1] is None:
            return "no site weather to compare"
        if abs(t - site_air[0]) > config.BAND_AIR_MAX_SITE_GAP_C:
            return "too far from the site weather (sensor in the sun or under a sleeve?)"
        return None
