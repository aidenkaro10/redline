"""
Labels: how bad was each hazard at each site on each day? (0-10, see hazards.py)

Sources, all turned into "records" (one row = one thing that happened):
  1. IEM NWS warnings archive   every watch/advisory/warning that covered the site
  2. IEM Local Storm Reports    storm reports within LSR_RADIUS_MILES of the site
  3. NOAA Storm Events          damage, injuries, deaths (needs the yearly CSVs downloaded)
  4. OSHA Severe Injury Reports heat injuries near the site (needs the OSHA zip)

Sources 3 and 4 are optional: if their files aren't downloaded yet, they're skipped.

Time zones matter:
  - IEM times are UTC.
  - Storm Events times are LOCAL STANDARD TIME (no daylight saving), with the
    offset in CZ_TIMEZONE, e.g. "EST-5".
  - Every record is moved to the SITE's local calendar date.

Run:  python -m ml.hazard_index.labels            (builds and saves labels.csv.gz)
"""

import argparse
import datetime as dt
import io
import zipfile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ml.hazard_index import fetch
from ml.hazard_index import hazards as H
from ml.hazard_index.sites import Site, distance_km, load_sites

IEM = "https://mesonet.agron.iastate.edu"
YEARS = tuple(range(2018, 2025))      # 2018-2024: train through 2022, test 2023-2024
UTC = dt.timezone.utc


def _utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def local_dates(start_utc: dt.datetime, end_utc: dt.datetime, tz: str) -> list[dt.date]:
    """Every local calendar date that the interval [start, end) touches."""
    z = ZoneInfo(tz)
    a, b = start_utc.astimezone(z), end_utc.astimezone(z)
    last = b.date() if b.time() != dt.time(0, 0) else b.date() - dt.timedelta(days=1)
    last = max(last, a.date())                 # an instant still touches its own day
    return [a.date() + dt.timedelta(days=i) for i in range((last - a.date()).days + 1)]


# ---------------------------------------------------------------------------
# 1. NWS warnings (IEM vtec_events_bypoint): matches the site's county or zone
# ---------------------------------------------------------------------------
def fetch_warnings(site: Site, year: int, offline: bool = False) -> list[dict]:
    d = fetch.get_json(f"{IEM}/json/vtec_events_bypoint.py",
                       dict(lat=site.lat, lon=site.lon, sdate=f"{year}-01-01", edate=f"{year}-12-31"),
                       offline=offline)
    return d.get("events", [])


def warning_interval(ev: dict) -> tuple[dt.datetime, dt.datetime]:
    """(start, end) in UTC. A few records have issue after expire; we sort them."""
    a, b = _utc(ev["issue"]), _utc(ev["expire"] or ev["issue"])
    return min(a, b), max(a, b)


def warning_records(site: Site, events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        hazard = H.VTEC_HAZARD.get(ev["phenomena"])
        level = H.VTEC_LEVEL.get(ev["significance"])
        if hazard is None or level is None:
            continue                              # fog, freeze, statements: not scored
        start, end = warning_interval(ev)
        for day in local_dates(start, end, site.timezone):
            out.append(dict(site_id=site.site_id, date=day, hazard=hazard, level=level,
                            source="NWS", detail=f"{ev['ph_name']} {ev['sig_name']}"))
    return out


# ---------------------------------------------------------------------------
# 2. Local Storm Reports (IEM lsrs_by_point): reports near the site
# ---------------------------------------------------------------------------
def fetch_lsrs(site: Site, year: int, offline: bool = False) -> list[dict]:
    d = fetch.get_json(f"{IEM}/api/1/nws/lsrs_by_point.json",
                       dict(lat=site.lat, lon=site.lon, radius_miles=H.LSR_RADIUS_MILES,
                            begints=f"{year}-01-01T00:00Z", endts=f"{year + 1}-01-01T00:00Z"),
                       offline=offline)
    return d.get("data", [])


def lsr_records(site: Site, reports: list[dict]) -> list[dict]:
    out = []
    for r in reports:
        kind = H.LSR_TYPE.get((r.get("typetext") or "").upper())
        if kind is None:
            continue
        hazard, level = kind
        when = _utc(r["valid"])
        day = when.astimezone(ZoneInfo(site.timezone)).date()
        out.append(dict(site_id=site.site_id, date=day, hazard=hazard, level=level,
                        source="LSR", detail=f"{r['typetext'].title()} near {r.get('city', '')}".strip()))
    return out


# ---------------------------------------------------------------------------
# 3. NOAA Storm Events (yearly details CSVs in data/raw/hazard_index/stormevents/)
# ---------------------------------------------------------------------------
def parse_damage(s) -> float:
    """'10.00K' -> 10000. Blank -> 0."""
    if not isinstance(s, str) or not s.strip():
        return 0.0
    s = s.strip().upper()
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(s[-1], 1.0)
    try:
        return float(s[:-1] if s[-1] in "KMB" else s) * mult
    except ValueError:
        return 0.0


def storm_event_time_utc(begin: str, cz_timezone: str) -> dt.datetime:
    """Storm Events times are local STANDARD time. 'EST-5' means UTC-5 all year."""
    local = dt.datetime.strptime(begin, "%d-%b-%y %H:%M:%S")
    digits = "".join(ch for ch in cz_timezone if ch.isdigit() or ch == "-")
    offset_h = int(digits[digits.index("-"):]) if "-" in digits else -int(digits or 5)
    return local.replace(tzinfo=dt.timezone(dt.timedelta(hours=offset_h))).astimezone(UTC)


def load_storm_events(years=YEARS) -> pd.DataFrame | None:
    """Florida rows from the downloaded yearly CSVs, or None if none are downloaded."""
    folder = fetch.data_dir() / "stormevents"
    frames = []
    for y in years:
        for path in sorted(folder.glob(f"StormEvents_details-ftp_v1.0_d{y}_c*.csv.gz")):
            df = pd.read_csv(path, dtype=str, low_memory=False)
            frames.append(df[df["STATE"] == "FLORIDA"])
    return pd.concat(frames, ignore_index=True) if frames else None


def storm_event_records(sites: list[Site], events: pd.DataFrame, site_ugcs: dict[str, set]) -> list[dict]:
    """
    An event counts for a site if it has coordinates within STORM_EVENT_RADIUS_KM,
    or (heat, tropical, many floods: no coordinates) its county/zone code is one
    of the codes NWS uses for that site. One storm creates many rows, so we keep
    one record per episode, site, day and hazard (the worst one).
    """
    out = []
    for _, e in events.iterrows():
        hazard = H.STORM_EVENT_HAZARD.get(e["EVENT_TYPE"])
        if hazard is None:
            continue
        hurt = sum(int(float(e.get(c) or 0)) for c in ("INJURIES_DIRECT", "INJURIES_INDIRECT"))
        dead = sum(int(float(e.get(c) or 0)) for c in ("DEATHS_DIRECT", "DEATHS_INDIRECT"))
        damage = parse_damage(e.get("DAMAGE_PROPERTY")) + parse_damage(e.get("DAMAGE_CROPS"))
        level = H.DEATH if dead else H.INJURY if hurt else H.DAMAGE if damage > 0 else H.REPORT
        when = storm_event_time_utc(e["BEGIN_DATE_TIME"], e["CZ_TIMEZONE"])
        ugc = f"FL{e['CZ_TYPE']}{int(float(e['CZ_FIPS'])):03d}"
        lat, lon = e.get("BEGIN_LAT"), e.get("BEGIN_LON")
        for s in sites:
            if isinstance(lat, str) and lat and isinstance(lon, str) and lon:
                near = distance_km(s.lat, s.lon, float(lat), float(lon)) <= H.STORM_EVENT_RADIUS_KM
            else:
                near = ugc in site_ugcs.get(s.site_id, set())
            if near:
                out.append(dict(site_id=s.site_id, date=when.astimezone(ZoneInfo(s.timezone)).date(),
                                hazard=hazard, level=level, source="StormEvents",
                                detail=f"{e['EVENT_TYPE']}" + (f", {hurt} hurt" if hurt else "") +
                                       (f", {dead} died" if dead else ""),
                                episode=e.get("EPISODE_ID")))
    if not out:
        return out
    df = pd.DataFrame(out).sort_values("level", ascending=False)
    df = df.drop_duplicates(["episode", "site_id", "date", "hazard"])     # dedupe by EPISODE_ID
    return df.drop(columns="episode").to_dict("records")


# ---------------------------------------------------------------------------
# 4. OSHA Severe Injury Reports (zip in data/raw/hazard_index/osha/)
# ---------------------------------------------------------------------------
def load_osha() -> pd.DataFrame | None:
    folder = fetch.data_dir() / "osha"
    zips = sorted(folder.glob("*.zip"))
    if not zips:
        return None
    with zipfile.ZipFile(zips[-1]) as z:
        name = next(n for n in z.namelist() if n.lower().endswith((".csv", ".xlsx")))
        raw = z.read(name)
    if name.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(raw), dtype=str, encoding="latin-1", low_memory=False)
    else:
        raise ValueError("OSHA file is xlsx; convert once to csv (no Excel reader installed)")
    df.columns = [c.strip() for c in df.columns]
    return df


def osha_records(sites: list[Site], osha: pd.DataFrame, years=YEARS) -> list[dict]:
    """
    Heat injuries (codes in OSHA_HEAT_CODES) within OSHA_RADIUS_KM -> heat INJURY label.
    Falls (42x, 43x) by utility / power line / electrical workers nearby are kept as
    records with hazard "fall": not a weather label, but the briefing lists them as
    past incidents near the site.
    """
    col = {c.lower(): c for c in osha.columns}
    ev, lat, lon, date = (col.get("event"), col.get("latitude"), col.get("longitude"), col.get("eventdate"))
    naics = col.get("primary naics")
    if not all((ev, lat, lon, date)):
        raise KeyError(f"OSHA columns not found; got {list(osha.columns)[:30]}")
    code = osha[ev].astype(str).str.strip()
    heat = code.isin(H.OSHA_HEAT_CODES)
    fall = code.str.startswith(H.OSHA_FALL_PREFIXES)
    if naics:
        fall &= osha[naics].astype(str).str.startswith(H.OSHA_FOCUS_NAICS)
    df = osha[heat | fall].copy()
    df["_heat"] = heat[heat | fall]
    df["_date"] = pd.to_datetime(df[date], errors="coerce", format="mixed").dt.date
    df["_lat"] = pd.to_numeric(df[lat], errors="coerce")
    df["_lon"] = pd.to_numeric(df[lon], errors="coerce")
    df = df[df["_date"].notna() & df["_lat"].notna() & df["_lon"].notna()]
    out = []
    for _, r in df.iterrows():
        if r["_date"].year not in years:
            continue
        for s in sites:
            if distance_km(s.lat, s.lon, r["_lat"], r["_lon"]) <= H.OSHA_RADIUS_KM:
                out.append(dict(site_id=s.site_id, date=r["_date"], hazard="heat" if r["_heat"] else "fall",
                                level=H.INJURY, source="OSHA",
                                detail=("Heat injury" if r["_heat"] else "Fall injury (utility/electrical work)")
                                       + " reported to OSHA nearby"))
    return out


# ---------------------------------------------------------------------------
# Put it all together
# ---------------------------------------------------------------------------
def records_to_labels(records: pd.DataFrame, sites: list[Site], years=YEARS) -> pd.DataFrame:
    """One row per site per day. One column per hazard (worst level), plus overall."""
    days = pd.date_range(f"{min(years)}-01-01", f"{max(years)}-12-31", freq="D").date
    grid = pd.MultiIndex.from_product([[s.site_id for s in sites], days], names=["site_id", "date"])
    labels = pd.DataFrame(0, index=grid, columns=list(H.HAZARDS), dtype=np.int64)
    if len(records):
        worst = records.groupby(["site_id", "date", "hazard"])["level"].max().unstack("hazard")
        worst = worst.reindex(columns=list(H.HAZARDS)).fillna(0).astype(np.int64)
        labels.update(worst.reindex(labels.index.intersection(worst.index)))
        labels = labels.astype(np.int64)
    labels["overall"] = labels[list(H.HAZARDS)].max(axis=1)
    return labels.reset_index()


def site_ugcs(warnings_by_site: dict[str, list[dict]]) -> dict[str, set]:
    """The county (FLC...) and zone (FLZ...) codes NWS used for each site."""
    return {sid: {ev["ugc"] for ev in evs if ev.get("ugc")} for sid, evs in warnings_by_site.items()}


def build_records(sites: list[Site], years=YEARS, offline: bool = False, verbose: bool = True) -> pd.DataFrame:
    recs, warn_by_site = [], {}
    for s in sites:
        warn_by_site[s.site_id] = []
        for y in years:
            if verbose:
                print(f"  IEM {s.site_id} {y}", flush=True)
            w = fetch_warnings(s, y, offline)
            warn_by_site[s.site_id] += w
            recs += warning_records(s, w)
            recs += lsr_records(s, fetch_lsrs(s, y, offline))
    se = load_storm_events(years)
    if se is not None:
        recs += storm_event_records(sites, se, site_ugcs(warn_by_site))
    elif verbose:
        print("  (Storm Events not downloaded yet: skipped)")
    osha = load_osha()
    if osha is not None:
        recs += osha_records(sites, osha, years)
    elif verbose:
        print("  (OSHA not downloaded yet: skipped)")
    df = pd.DataFrame(recs, columns=["site_id", "date", "hazard", "level", "source", "detail"])
    first, last = dt.date(min(years), 1, 1), dt.date(max(years), 12, 31)
    return df[(df["date"] >= first) & (df["date"] <= last)].reset_index(drop=True)


def labels_path():
    return fetch.data_dir() / "labels.csv.gz"


def records_path():
    return fetch.data_dir() / "records.csv.gz"


def load_labels() -> pd.DataFrame:
    df = pd.read_csv(labels_path(), parse_dates=["date"])
    df["date"] = df["date"].dt.date
    return df


def load_records() -> pd.DataFrame:
    df = pd.read_csv(records_path(), parse_dates=["date"])
    df["date"] = df["date"].dt.date
    return df


def main():
    ap = argparse.ArgumentParser(description="Build hazard index labels")
    ap.add_argument("--sites", default=None, help="auto, geojson or placeholder")
    ap.add_argument("--offline", action="store_true", help="use only cached downloads")
    args = ap.parse_args()
    sites = load_sites(args.sites)
    print(f"Building labels for {len(sites)} sites, {YEARS[0]}-{YEARS[-1]}")
    recs = build_records(sites, offline=args.offline)
    labels = records_to_labels(recs, sites)
    recs.to_csv(records_path(), index=False)
    labels.to_csv(labels_path(), index=False)
    print(f"Saved {len(recs)} records and {len(labels)} site-days to {fetch.data_dir()}")
    print("Share of site-days at each level, per hazard:")
    for h in list(H.HAZARDS) + ["overall"]:
        print(f"  {h:9s} " + "  ".join(f">={k}: {np.mean(labels[h] >= k):6.1%}" for k in (H.ADVISORY, H.WARNING, H.DAMAGE)))


if __name__ == "__main__":
    main()
