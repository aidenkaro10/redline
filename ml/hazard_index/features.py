"""
Features for "how dangerous is site S tomorrow (day D)?"

THE RULE: a feature for day D may only use information that existed by
6 PM local time on day D-1 (the 6 PM briefing). We call that moment the CUTOFF.
test_features.py breaks the data after the cutoff on purpose and checks that
no feature changes. If you add a feature, that test protects you.

Features (one row per site per day D):
  Weather, from the Open-Meteo archive (ERA5), hours up to the cutoff only:
    hi_max_today      max heat index (°F) on D-1 until 6 PM. Our "forecast" for
                      tomorrow's heat is persistence: archived day-ahead forecasts
                      don't exist for 2018-2022, so we don't pretend to have them.
    hi_max_3d         max heat index over the 3 days before the cutoff
    t_max_today, td_mean_today, gust_max_today, rh_min_today (dry air feeds fires)
    rain_24h, rain_72h, rain_7d   rain (mm) in the hours before the cutoff
    soil_0_7, soil_7_28           soil moisture (m³/m³) at the cutoff
  Forecasts issued before the cutoff:
    spc_day2_cat      SPC Day 2 convective outlook for D (0 none .. 6 high risk)
    (SPC fire weather outlooks are left out: IEM can't search their history by
    point, only one day at a time, which would be ~30,000 calls.)
  NWS products, issued before the cutoff:
    nws_now_<hazard>  worst watch/advisory/warning in effect AT the cutoff
    nws_24h_<hazard>  worst one issued in the 24 h before the cutoff
    lsr_24h           storm reports near the site in the 24 h before the cutoff
  Site facts (static.py): flood_zone_score, levee, gauge_km
  Season: doy_sin, doy_cos

Run:  python -m ml.hazard_index.features        (downloads weather, builds features.csv.gz)
"""

import argparse
import bisect
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ml.hazard_index import fetch
from ml.hazard_index import hazards as H
from ml.hazard_index import labels as L
from ml.hazard_index import static
from ml.hazard_index.sites import Site, load_sites

CUTOFF_HOUR = 18             # 6 PM local, the evening before
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
# A year of hourly data counts as ~26 calls against Open-Meteo's free limits
# (10,000 a day, 5,000 an hour). 25 s apart keeps us near 3,700 an hour.
OPEN_METEO_DELAY_S = 25.0
WEATHER_GRID_DEG = 0.1       # sites in the same 0.1° cell (~10 km) share one weather download
# Dew point isn't downloaded: it's computed from temperature and humidity (saves budget).
HOURLY_VARS = ("temperature_2m", "relative_humidity_2m", "precipitation",
               "wind_gusts_10m", "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm")

SPC_CAT = {"TSTM": 1, "MRGL": 2, "SLGT": 3, "ENH": 4, "MDT": 5, "HIGH": 6}

WEATHER_FEATURES = ["hi_max_today", "hi_max_3d", "t_max_today", "td_mean_today", "rh_min_today",
                    "gust_max_today", "rain_24h", "rain_72h", "rain_7d", "soil_0_7", "soil_7_28"]
OUTLOOK_FEATURES = ["spc_day2_cat"]
NWS_FEATURES = [f"nws_now_{h}" for h in H.HAZARDS] + [f"nws_24h_{h}" for h in H.HAZARDS] + ["lsr_24h"]
STATIC_FEATURES = ["flood_zone_score", "levee", "gauge_km"]
SEASON_FEATURES = ["doy_sin", "doy_cos"]
FEATURES = WEATHER_FEATURES + OUTLOOK_FEATURES + NWS_FEATURES + STATIC_FEATURES + SEASON_FEATURES


def cutoff_utc(day: dt.date, tz: str) -> dt.datetime:
    """6 PM local on the day BEFORE `day`, as a UTC time."""
    prev = day - dt.timedelta(days=1)
    local = dt.datetime(prev.year, prev.month, prev.day, CUTOFF_HOUR, tzinfo=ZoneInfo(tz))
    return local.astimezone(dt.timezone.utc)


def dew_point_c(t_c: np.ndarray, rh: np.ndarray) -> np.ndarray:
    """Dew point from temperature and humidity (Magnus formula, good to ~0.2 °C)."""
    a, b = 17.62, 243.12
    g = np.log(np.clip(rh, 1, 100) / 100) + a * t_c / (b + t_c)
    return b * g / (a - g)


def heat_index_f(t_c: np.ndarray, rh: np.ndarray) -> np.ndarray:
    """NWS heat index (Rothfusz regression, with the NWS low-heat formula). Returns °F."""
    t = t_c * 9 / 5 + 32
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    full = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
            - 6.83783e-3 * t * t - 5.481717e-2 * rh * rh + 1.22874e-3 * t * t * rh
            + 8.5282e-4 * t * rh * rh - 1.99e-6 * t * t * rh * rh)
    # NWS adjustments for very dry and very humid air
    dry = (rh < 13) & (t >= 80) & (t <= 112)
    full = np.where(dry, full - ((13 - rh) / 4) * np.sqrt(np.clip((17 - np.abs(t - 95)) / 17, 0, None)), full)
    wet = (rh > 85) & (t >= 80) & (t <= 87)
    full = np.where(wet, full + ((rh - 85) / 10) * ((87 - t) / 5), full)
    return np.where((simple + t) / 2 < 80, simple, full)


# ---------------------------------------------------------------------------
# Downloads (cached)
# ---------------------------------------------------------------------------
def fetch_weather(site: Site, years=L.YEARS, offline: bool = False) -> pd.DataFrame:
    """Hourly ERA5 weather in UTC, one cached call per weather cell per year (plus 10 days before)."""
    lat = round(round(site.lat / WEATHER_GRID_DEG) * WEATHER_GRID_DEG, 2)
    lon = round(round(site.lon / WEATHER_GRID_DEG) * WEATHER_GRID_DEG, 2)
    frames = []
    for y in years:
        start = dt.date(y, 1, 1) - dt.timedelta(days=10) if y == min(years) else dt.date(y, 1, 1)
        d = fetch.get_json(OPEN_METEO_ARCHIVE, dict(
            latitude=lat, longitude=lon, start_date=start.isoformat(),
            end_date=f"{y}-12-31", hourly=",".join(HOURLY_VARS), timezone="GMT"),
            offline=offline, delay=OPEN_METEO_DELAY_S)
        frames.append(pd.DataFrame(d["hourly"]))
    w = pd.concat(frames, ignore_index=True)
    w["time"] = pd.to_datetime(w["time"], utc=True)
    return w.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def fetch_spc(site: Site, offline: bool = False) -> list[dict]:
    """Every SPC Day 2 convective outlook that put this point in a risk area."""
    d = fetch.get_json("https://mesonet.agron.iastate.edu/json/spcoutlook.py",
                       dict(lat=site.lat, lon=site.lon, last=0, day=2, cat="CATEGORICAL"), offline=offline)
    return parse_spc(d)


def parse_spc(d: dict) -> list[dict]:
    return [dict(kind="cat", level=SPC_CAT[o["threshold"]], valid=L._utc(o["utc_issue"]),
                 issued=L._utc(o["utc_product_issue"]))
            for o in d.get("outlooks", []) if o.get("threshold") in SPC_CAT]


# ---------------------------------------------------------------------------
# The feature builder. Pure function: data in, features out. Every input
# carries its own time, and only rows at or before each day's cutoff are used.
# ---------------------------------------------------------------------------
def build_site_features(site: Site, days: list[dt.date], weather: pd.DataFrame,
                        warnings: list[dict], lsrs: list[dict], spc: list[dict], facts: dict) -> pd.DataFrame:
    tz = site.timezone
    cut = [cutoff_utc(d, tz) for d in days]

    # --- weather: index of the last hour at or before each cutoff -----------------
    t = weather["time"].to_numpy(dtype="datetime64[ns]")
    hi = heat_index_f(weather["temperature_2m"].to_numpy(float), weather["relative_humidity_2m"].to_numpy(float))
    temp = weather["temperature_2m"].to_numpy(float)
    rh = weather["relative_humidity_2m"].to_numpy(float)
    dew = (weather["dew_point_2m"].to_numpy(float) if "dew_point_2m" in weather
           else dew_point_c(temp, weather["relative_humidity_2m"].to_numpy(float)))
    gust = weather["wind_gusts_10m"].to_numpy(float)
    rain = np.nan_to_num(weather["precipitation"].to_numpy(float))
    rain_cum = np.concatenate([[0.0], np.cumsum(rain)])
    soil1 = weather["soil_moisture_0_to_7cm"].to_numpy(float)
    soil2 = weather["soil_moisture_7_to_28cm"].to_numpy(float)
    cut64 = np.array([np.datetime64(c.replace(tzinfo=None), "ns") for c in cut])
    end = np.searchsorted(t, cut64, side="right")          # rows [.. end) are <= cutoff

    rows = []
    for i, day in enumerate(days):
        e = end[i]
        prev = day - dt.timedelta(days=1)
        midnight = dt.datetime(prev.year, prev.month, prev.day, tzinfo=ZoneInfo(tz)).astimezone(dt.timezone.utc)
        s_today = np.searchsorted(t, np.datetime64(midnight.replace(tzinfo=None), "ns"))
        s_3d = max(e - 72, 0)
        r = dict(site_id=site.site_id, date=day)

        def window_max(a, s):
            seg = a[s:e]
            return float(np.nanmax(seg)) if len(seg) and not np.all(np.isnan(seg)) else np.nan

        r["hi_max_today"] = window_max(hi, s_today)
        r["hi_max_3d"] = window_max(hi, s_3d)
        r["t_max_today"] = window_max(temp, s_today)
        seg = dew[s_today:e]
        r["td_mean_today"] = float(np.nanmean(seg)) if len(seg) and not np.all(np.isnan(seg)) else np.nan
        r["gust_max_today"] = window_max(gust, s_today)
        r["rh_min_today"] = -window_max(-rh, s_today)
        for name, hours in (("rain_24h", 24), ("rain_72h", 72), ("rain_7d", 168)):
            r[name] = float(rain_cum[e] - rain_cum[max(e - hours, 0)]) if e > 0 else np.nan
        r["soil_0_7"] = float(soil1[e - 1]) if e > 0 else np.nan
        r["soil_7_28"] = float(soil2[e - 1]) if e > 0 else np.nan
        rows.append(r)
    df = pd.DataFrame(rows)

    # --- SPC Day 2 outlooks valid for D (12Z on D) and issued before the cutoff -----
    cat = np.zeros(len(days))
    by_valid: dict[dt.date, list[dict]] = {}
    for o in spc:
        by_valid.setdefault(o["valid"].date(), []).append(o)
    for i, day in enumerate(days):
        for o in by_valid.get(day, []):
            if o["issued"] <= cut[i] and o["kind"] == "cat":
                cat[i] = max(cat[i], o["level"])
    df["spc_day2_cat"] = cat

    # --- NWS products and storm reports known by the cutoff -------------------------
    now = {h: np.zeros(len(days)) for h in H.HAZARDS}
    last24 = {h: np.zeros(len(days)) for h in H.HAZARDS}
    prods = []
    for ev in warnings:
        hz, lvl = H.VTEC_HAZARD.get(ev["phenomena"]), H.VTEC_LEVEL.get(ev["significance"])
        if hz and lvl:
            a, b = L.warning_interval(ev)
            prods.append((a, b, hz, lvl))
    prods.sort(key=lambda p: p[0])
    starts = [p[0] for p in prods]
    for i, c in enumerate(cut):
        # products that started up to 30 days before the cutoff (long river floods included)
        lo = bisect.bisect_left(starts, c - dt.timedelta(days=30))
        hi_ = bisect.bisect_right(starts, c)                  # started at or before the cutoff
        for a, b, hz, lvl in prods[lo:hi_]:
            if b > c:
                now[hz][i] = max(now[hz][i], lvl)
            if a > c - dt.timedelta(hours=24):
                last24[hz][i] = max(last24[hz][i], lvl)
    for h in H.HAZARDS:
        df[f"nws_now_{h}"], df[f"nws_24h_{h}"] = now[h], last24[h]

    lsr_times = sorted(L._utc(r["valid"]) for r in lsrs if (r.get("typetext") or "").upper() in H.LSR_TYPE)
    df["lsr_24h"] = [bisect.bisect_right(lsr_times, c) - bisect.bisect_right(lsr_times, c - dt.timedelta(hours=24))
                     for c in cut]

    # --- site facts and season --------------------------------------------------
    for k in STATIC_FEATURES:
        df[k] = float(facts[k])
    doy = np.array([d.timetuple().tm_yday for d in days])
    df["doy_sin"], df["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    return df


def build_features(sites: list[Site], years=L.YEARS, offline: bool = False, verbose: bool = True) -> pd.DataFrame:
    days = list(pd.date_range(f"{min(years)}-01-01", f"{max(years)}-12-31", freq="D").date)
    frames, failed = [], []
    for n, s in enumerate(sites, start=1):
        if verbose:
            print(f"  features {s.site_id} ({n}/{len(sites)})", flush=True)
        try:
            weather = fetch_weather(s, years, offline)
        except Exception as e:                    # e.g. Open-Meteo's daily limit: skip, rerun later
            print(f"    skipped {s.site_id}: {str(e)[:120]}", flush=True)
            failed.append(s.site_id)
            continue
        warnings, lsrs = [], []
        for y in years:
            warnings += L.fetch_warnings(s, y, offline)
            lsrs += L.fetch_lsrs(s, y, offline)
        # the products in effect on Jan 1 of the first year were issued the year before
        warnings += L.fetch_warnings(s, min(years) - 1, offline)
        frames.append(build_site_features(s, days, weather, warnings, lsrs, fetch_spc(s, offline),
                                          static.site_static(s, offline)))
    if failed:
        print(f"  {len(failed)} sites skipped (rerun later; finished downloads are cached): {failed}")
    return pd.concat(frames, ignore_index=True)


def features_path():
    return fetch.data_dir() / "features.csv.gz"


def load_features() -> pd.DataFrame:
    df = pd.read_csv(features_path(), parse_dates=["date"])
    df["date"] = df["date"].dt.date
    return df


def main():
    ap = argparse.ArgumentParser(description="Build hazard index features")
    ap.add_argument("--sites", default=None, help="auto, geojson or placeholder")
    ap.add_argument("--offline", action="store_true", help="use only cached downloads")
    args = ap.parse_args()
    sites = load_sites(args.sites)
    print(f"Building features for {len(sites)} sites, {L.YEARS[0]}-{L.YEARS[-1]}")
    df = build_features(sites, offline=args.offline)
    df.to_csv(features_path(), index=False)
    print(f"Saved {len(df)} rows x {len(FEATURES)} features to {features_path()}")
    print(df[FEATURES].describe().T[["mean", "min", "max"]].round(2).to_string())


if __name__ == "__main__":
    main()
