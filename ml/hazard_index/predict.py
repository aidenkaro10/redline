"""
predict_index(site_id, date): tomorrow's 0-10 hazard index for one site.

Used by the 6 PM briefing (session B) and the risk-ranked map (session E).
`date` is the day being scored (usually tomorrow). Only data known by 6 PM
local the evening before is used, exactly like in training.

What the number means: the index is the EXPECTED level on the label ladder
(watch 1, advisory 3, warning 5, damage 8, death 10), so
    index = 3 * P(advisory or worse) + 2 * P(warning or worse)
          + 3 * P(damage or worse)   + 2 * P(death)
A day that will surely get a heat advisory and nothing else scores about 3.
The overall index has its own models (worst hazard of the day), so it is not
just the max of the hazard indexes.

The index ranks sites and fills the briefing. It is NOT an alert and never
makes a safety call: live alerts come from backend/hazards and backend/alerts.

Where the data comes from:
  - dates more than ARCHIVE_LAG_DAYS ago: the Open-Meteo archive (ERA5), same as training
  - recent dates and tomorrow: the Open-Meteo forecast API (past days + today), and
    live IEM warnings / storm reports / SPC outlooks (re-downloaded at most hourly)

Try it:  python -m ml.hazard_index.predict ph-gainesville 2024-07-10
"""

import argparse
import datetime as dt
import functools
import json
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ml.hazard_index import fetch
from ml.hazard_index import hazards as H
from ml.hazard_index import labels as L
from ml.hazard_index import static
from ml.hazard_index.features import (HOURLY_VARS, OPEN_METEO_ARCHIVE, OPEN_METEO_DELAY_S, parse_spc,
                                      build_site_features, cutoff_utc)
import joblib

from ml.hazard_index.sites import Site, distance_km, get_site, load_sites
from ml.hazard_index.train import MODEL_PATH

ARCHIVE_LAG_DAYS = 7          # the ERA5 archive runs ~5 days behind; keep a margin
LIVE_MAX_AGE_S = 3600         # live sources are re-downloaded at most once an hour
STEPS = ((H.ADVISORY, 3.0), (H.WARNING, 2.0), (H.DAMAGE, 3.0))   # index = sum(step * P(>= level))
DEATH_STEP = 2.0

# Display bands for the map and briefing. These are labels, not alert thresholds.
INDEX_BANDS = ((2.0, "low"), (4.0, "moderate"), (6.0, "high"), (10.01, "extreme"))

OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
FORECAST_SOIL = ("soil_moisture_0_to_1cm", "soil_moisture_1_to_3cm", "soil_moisture_3_to_9cm",
                 "soil_moisture_9_to_27cm")


@functools.lru_cache(maxsize=1)
def load_model(path: str = str(MODEL_PATH)) -> dict:
    """The trained models. A model is None when there were too few yes-days (climatology is used)."""
    return joblib.load(path)


def band(index: float) -> str:
    return next(name for upper, name in INDEX_BANDS if index < upper)


def index_from_probs(p_by_level: dict[int, float], death_ratio: float) -> float:
    return float(sum(step * p_by_level[lv] for lv, step in STEPS) + DEATH_STEP * p_by_level[H.DAMAGE] * death_ratio)


# ---------------------------------------------------------------------------
# Gather the inputs for one site and day (archive for old dates, live for recent)
# ---------------------------------------------------------------------------
def _weather_archive(site: Site, day: dt.date, offline: bool) -> pd.DataFrame:
    d = fetch.get_json(OPEN_METEO_ARCHIVE, dict(
        latitude=site.lat, longitude=site.lon, start_date=(day - dt.timedelta(days=9)).isoformat(),
        end_date=day.isoformat(), hourly=",".join(HOURLY_VARS), timezone="GMT"),
        offline=offline, delay=OPEN_METEO_DELAY_S)
    w = pd.DataFrame(d["hourly"])
    w["time"] = pd.to_datetime(w["time"], utc=True)
    return w


def _weather_live(site: Site, offline: bool) -> pd.DataFrame:
    """Recent hours from the forecast API. Its soil layers differ from ERA5's, so we
    map them: 0-7 cm ~ depth-weighted mix of 0-1, 1-3, 3-9 cm; 7-28 cm ~ 9-27 cm."""
    hourly = [v for v in HOURLY_VARS if not v.startswith("soil")] + list(FORECAST_SOIL)
    d = fetch.get_json(OPEN_METEO_FORECAST, dict(
        latitude=site.lat, longitude=site.lon, hourly=",".join(hourly), past_days=9,
        forecast_days=2, timezone="GMT"), offline=offline, max_age_s=LIVE_MAX_AGE_S)
    w = pd.DataFrame(d["hourly"])
    w["time"] = pd.to_datetime(w["time"], utc=True)
    s01, s13, s39, s927 = (w[c].astype(float) for c in FORECAST_SOIL)
    w["soil_moisture_0_to_7cm"] = (1 * s01 + 2 * s13 + 4 * s39) / 7
    w["soil_moisture_7_to_28cm"] = s927
    return w


def _spc(site: Site, live: bool, offline: bool) -> list[dict]:
    d = fetch.get_json("https://mesonet.agron.iastate.edu/json/spcoutlook.py",
                       dict(lat=site.lat, lon=site.lon, last=5 if live else 0, day=2, cat="CATEGORICAL"),
                       offline=offline, max_age_s=LIVE_MAX_AGE_S if live else None)
    return parse_spc(d)


def gather_inputs(site: Site, day: dt.date, offline: bool = False, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    live = day > today - dt.timedelta(days=ARCHIVE_LAG_DAYS)
    age = LIVE_MAX_AGE_S if live else None
    weather = _weather_live(site, offline) if live else _weather_archive(site, day, offline)
    warnings = fetch.get_json(f"{L.IEM}/json/vtec_events_bypoint.py",
                              dict(lat=site.lat, lon=site.lon, sdate=(day - dt.timedelta(days=31)).isoformat(),
                                   edate=day.isoformat()), offline=offline, max_age_s=age).get("events", [])
    lsrs = fetch.get_json(f"{L.IEM}/api/1/nws/lsrs_by_point.json",
                          dict(lat=site.lat, lon=site.lon, radius_miles=H.LSR_RADIUS_MILES,
                               begints=f"{day - dt.timedelta(days=2)}T00:00Z", endts=f"{day}T00:00Z"),
                          offline=offline, max_age_s=age).get("data", [])
    return dict(weather=weather, warnings=warnings, lsrs=lsrs, spc=_spc(site, live, offline),
                facts=static.site_static(site, offline), mode="live" if live else "archive")


# ---------------------------------------------------------------------------
# Plain-language reasons
# ---------------------------------------------------------------------------
_SPC_NAME = {1: "general thunder", 2: "marginal risk", 3: "slight risk", 4: "enhanced risk",
             5: "moderate risk", 6: "high risk"}
_NWS_WORD = {H.WATCH: "watch", H.ADVISORY: "advisory", H.WARNING: "warning"}


def describe(feature: str, value: float) -> str:
    v = value
    if feature == "hi_max_today":
        return f"Heat index reached {v:.0f}°F by 6 PM today"
    if feature == "hi_max_3d":
        return f"Heat index up to {v:.0f}°F over the last 3 days"
    if feature == "t_max_today":
        return f"High of {v * 9 / 5 + 32:.0f}°F today"
    if feature == "td_mean_today":
        return f"Humid air today (dew point {v * 9 / 5 + 32:.0f}°F)"
    if feature == "gust_max_today":
        return f"Wind gusts to {v / 1.609:.0f} mph today"
    if feature.startswith("rain_"):
        span = {"rain_24h": "24 hours", "rain_72h": "3 days", "rain_7d": "7 days"}[feature]
        return f"{v / 25.4:.1f} in of rain in the last {span}"
    if feature.startswith("soil_"):
        return f"Soil moisture {v:.2f} (wetter soil floods sooner)"
    if feature == "spc_day2_cat":
        return f"SPC storm outlook for tomorrow: {_SPC_NAME.get(int(v), 'no risk area')}"
    if feature == "rh_min_today":
        return f"Humidity dropped to {v:.0f}% today"
    if feature.startswith("nws_now_"):
        hz = feature.removeprefix("nws_now_")
        return f"NWS {hz} {_NWS_WORD.get(int(v), 'product')} in effect at 6 PM" if v else f"No NWS {hz} products in effect"
    if feature.startswith("nws_24h_"):
        hz = feature.removeprefix("nws_24h_")
        return f"NWS {hz} {_NWS_WORD.get(int(v), 'product')} issued in the last 24 hours" if v else f"No NWS {hz} products today"
    if feature == "lsr_24h":
        return f"{int(v)} storm reports nearby in the last 24 hours"
    if feature == "flood_zone_score":
        return "Site sits in a FEMA flood zone" if v >= 3 else "Site's FEMA flood zone"
    if feature == "levee":
        return "Site is next to a levee"
    if feature == "gauge_km":
        return f"Nearest river gauge {v:.0f} km away"
    if feature == "season":
        return "Time of year"
    return feature


def _hazard_probs(model: dict, X: np.ndarray, clim: dict) -> dict:
    """P(level or worse) for every hazard and level, for each row of X.
    Higher levels are capped at lower ones (they can't be likelier)."""
    probs = {}
    for hz in list(H.HAZARDS) + ["overall"]:
        prev = np.ones(len(X))
        probs[hz] = {}
        for lv, _ in STEPS:
            mdl = model["models"][hz].get(str(lv))
            p = mdl.predict(X) if mdl is not None else np.full(len(X), clim[hz][lv])
            prev = np.minimum(p, prev)
            probs[hz][lv] = prev
    return probs


def _hazard_index(model: dict, probs: dict, hz: str) -> np.ndarray:
    p = probs[hz]
    return sum(step * p[lv] for lv, step in STEPS) + DEATH_STEP * p[H.DAMAGE] * model["death_given_damage"][hz]


def _reasons(model: dict, x: np.ndarray, clim: dict, n: int = 3) -> list[dict]:
    """
    Which inputs pushed each hazard's index up? For each input we swap in a
    typical value (the training median) and see how much the index drops.
    The drop is that input's "points". Season is sin + cos of the day of year,
    so those two are swapped together.
    """
    feats = model["features"]
    groups = {}
    for j, f in enumerate(feats):
        groups.setdefault("season" if f.startswith("doy_") else f, []).append(j)
    names = list(groups)
    X = np.repeat(x[None, :], len(names) + 1, axis=0)       # row 0 = as is, row k = input k swapped
    for k, name in enumerate(names, start=1):
        for j in groups[name]:
            med = model["feature_median"][feats[j]]
            X[k, j] = med if med is not None else X[k, j]
    probs = _hazard_probs(model, X, clim)
    pushes = []
    for hz in H.HAZARDS:
        idx = _hazard_index(model, probs, hz)
        for k, name in enumerate(names, start=1):
            if idx[0] - idx[k] > 0.05:
                pushes.append((float(idx[0] - idx[k]), hz, name))
    pushes.sort(reverse=True)
    out = []
    for val, hz, f in pushes[:n]:
        value = None if f == "season" else float(x[feats.index(f)])
        out.append(dict(hazard=hz, feature=f, value=value, points=round(val, 2),
                        text=describe(f, value if value is not None else 0.0)))
    return out


# ---------------------------------------------------------------------------
# The public function
# ---------------------------------------------------------------------------
def predict_index(site_id: str, date, offline: bool = False, sites: list[Site] | None = None,
                  inputs: dict | None = None, today: dt.date | None = None) -> dict:
    """
    0-10 hazard index for `site_id` on `date` (a date or 'YYYY-MM-DD'), with a
    per-hazard breakdown and the top reasons. See docs/interfaces/hazard-index.md.
    `inputs` lets tests (and replay mode) pass the raw data in instead of downloading.
    """
    day = dt.date.fromisoformat(date) if isinstance(date, str) else date
    site = get_site(site_id, sites)
    model = load_model()
    inputs = inputs or gather_inputs(site, day, offline, today)
    row = build_site_features(site, [day], inputs["weather"], inputs["warnings"], inputs["lsrs"],
                              inputs["spc"], inputs["facts"]).iloc[0]
    feats = model["features"]
    x = row[feats].to_numpy(float)
    missing = [f for f, v in zip(feats, x) if np.isnan(v)]
    med = model["feature_median"]
    x = np.array([med[f] if np.isnan(v) and med[f] is not None else v for f, v in zip(feats, x)])

    # nearest trained site, for the climatology baseline of an unseen site
    xy = model["site_xy"]
    clim_site = site.site_id if site.site_id in model["sites"] else \
        min(xy, key=lambda s: distance_km(site.lat, site.lon, *xy[s]))

    # climatology: how often each level happens this month at this (or the nearest) site
    clim = {hz: {lv: (lambda c: c["site_month"].get(f"{clim_site}|{day.month}",
                                                    c["month_rate"].get(str(day.month), 0.0)))(
                     model["climatology"][hz][str(lv)]) for lv, _ in STEPS}
            for hz in list(H.HAZARDS) + ["overall"]}
    probs = _hazard_probs(model, x[None, :], clim)
    out_h = {}
    for hz in list(H.HAZARDS) + ["overall"]:
        p = {lv: float(v[0]) for lv, v in probs[hz].items()}
        pc, prev = {}, 1.0
        for lv, _ in STEPS:
            prev = min(clim[hz][lv], prev)
            pc[lv] = prev
        ratio = model["death_given_damage"][hz]
        out_h[hz] = dict(index=round(index_from_probs(p, ratio), 2),
                         p_advisory=round(p[H.ADVISORY], 3), p_warning=round(p[H.WARNING], 3),
                         p_damage=round(p[H.DAMAGE], 3),
                         climatology_index=round(index_from_probs(pc, ratio), 2))
    overall = out_h.pop("overall")
    cut = cutoff_utc(day, site.timezone)
    notes = []
    if model.get("site_source") == "placeholder":
        notes.append("Trained on placeholder sites; retrain once backend/sites/plants.geojson lands.")
    if site.site_id not in model["sites"]:
        notes.append(f"Site not in training data; climatology borrowed from {clim_site}.")
    if missing:
        notes.append(f"Missing inputs filled with typical values: {', '.join(missing)}")
    if inputs["mode"] == "live" and dt.datetime.now(dt.timezone.utc) < cut:
        notes.append("Called before 6 PM: hours until 6 PM come from the weather forecast.")
    return dict(
        site_id=site.site_id, site_name=site.name, date=day.isoformat(),
        index=overall["index"], band=band(overall["index"]),
        climatology_index=overall["climatology_index"],
        p_advisory=overall["p_advisory"], p_warning=overall["p_warning"], p_damage=overall["p_damage"],
        hazards=out_h,
        top_reasons=_reasons(model, x, clim),
        cutoff=cut.astimezone(ZoneInfo(site.timezone)).isoformat(),
        data_mode=inputs["mode"], model_version=model["version"], notes=notes,
    )


def predict_all_sites(date, offline: bool = False) -> list[dict]:
    """Every site, ranked by index (highest first). For the map."""
    sites = load_sites()
    out = [predict_index(s.site_id, date, offline=offline, sites=sites) for s in sites]
    return sorted(out, key=lambda r: -r["index"])


def main():
    ap = argparse.ArgumentParser(description="Hazard index for one site and day")
    ap.add_argument("site_id")
    ap.add_argument("date", help="YYYY-MM-DD, the day being scored (e.g. tomorrow)")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    print(json.dumps(predict_index(args.site_id, args.date, offline=args.offline), indent=2))


if __name__ == "__main__":
    main()
