import datetime as dt

import pandas as pd

from ml.hazard_index import hazards as H
from ml.hazard_index import labels as L
from ml.hazard_index.sites import Site

GNV = Site("gnv", "Gainesville", 29.65, -82.32)
PNS = Site("pns", "Pensacola", 30.57, -87.23)       # Central time


def test_heat_codes_eh_and_xh_both_map_to_heat():
    assert H.VTEC_HAZARD["EH"] == H.VTEC_HAZARD["XH"] == H.VTEC_HAZARD["HT"] == "heat"


def test_warning_lands_on_local_dates():
    # 02:00 UTC on Jun 6 is 10 PM on Jun 5 in Gainesville (EDT, UTC-4)
    ev = dict(phenomena="SV", significance="W", issue="2024-06-06T02:00:00Z",
              expire="2024-06-06T02:30:00Z", ph_name="Severe Thunderstorm", sig_name="Warning", ugc="FLC001")
    recs = L.warning_records(GNV, [ev])
    assert [r["date"] for r in recs] == [dt.date(2024, 6, 5)]
    assert recs[0]["hazard"] == "storm" and recs[0]["level"] == H.WARNING


def test_multi_day_warning_covers_each_day_and_ignores_fog():
    ev = dict(phenomena="FL", significance="W", issue="2024-08-05T14:00:00Z",
              expire="2024-08-08T14:00:00Z", ph_name="Flood", sig_name="Warning", ugc="FLC001")
    fog = dict(ev, phenomena="FG", significance="Y")
    recs = L.warning_records(GNV, [ev, fog])
    assert [r["date"].day for r in recs] == [5, 6, 7, 8]


def test_swapped_issue_expire_is_handled():
    ev = dict(phenomena="FL", significance="W", issue="2024-01-13T15:00:00Z",
              expire="2024-01-12T01:00:00Z", ph_name="Flood", sig_name="Warning", ugc="FLC001")
    assert len(L.warning_records(GNV, [ev])) == 3       # Jan 11, 12, 13 (local)
    assert L.warning_records(GNV, [ev])[0]["date"] == dt.date(2024, 1, 11)


def test_storm_events_local_standard_time():
    # 11:30 PM CST on Jun 30 = 05:30 UTC Jul 1. In Pensacola (CDT in summer) it is
    # 12:30 AM Jul 1 local daylight time, so the event belongs to Jul 1.
    t = L.storm_event_time_utc("30-JUN-24 23:30:00", "CST-6")
    assert t == dt.datetime(2024, 7, 1, 5, 30, tzinfo=dt.timezone.utc)


def test_storm_events_dedupe_by_episode_and_zone_match():
    rows = pd.DataFrame([
        # two rows from one episode, both near Gainesville: one record, worst level kept
        dict(EVENT_TYPE="Thunderstorm Wind", INJURIES_DIRECT="0", INJURIES_INDIRECT="0",
             DEATHS_DIRECT="0", DEATHS_INDIRECT="0", DAMAGE_PROPERTY="10.00K", DAMAGE_CROPS="",
             BEGIN_DATE_TIME="05-JUN-24 16:00:00", CZ_TIMEZONE="EST-5", CZ_TYPE="C", CZ_FIPS="1",
             BEGIN_LAT="29.66", BEGIN_LON="-82.30", EPISODE_ID="1"),
        dict(EVENT_TYPE="Thunderstorm Wind", INJURIES_DIRECT="1", INJURIES_INDIRECT="0",
             DEATHS_DIRECT="0", DEATHS_INDIRECT="0", DAMAGE_PROPERTY="", DAMAGE_CROPS="",
             BEGIN_DATE_TIME="05-JUN-24 16:10:00", CZ_TIMEZONE="EST-5", CZ_TYPE="C", CZ_FIPS="1",
             BEGIN_LAT="29.70", BEGIN_LON="-82.35", EPISODE_ID="1"),
        # heat has no coordinates: matched by forecast zone code
        dict(EVENT_TYPE="Heat", INJURIES_DIRECT="0", INJURIES_INDIRECT="0",
             DEATHS_DIRECT="1", DEATHS_INDIRECT="0", DAMAGE_PROPERTY="", DAMAGE_CROPS="",
             BEGIN_DATE_TIME="10-JUL-24 12:00:00", CZ_TIMEZONE="EST-5", CZ_TYPE="Z", CZ_FIPS="136",
             BEGIN_LAT="", BEGIN_LON="", EPISODE_ID="2"),
    ])
    recs = L.storm_event_records([GNV], rows, {"gnv": {"FLZ136"}})
    assert len(recs) == 2
    storm = next(r for r in recs if r["hazard"] == "storm")
    assert storm["level"] == H.INJURY
    heat = next(r for r in recs if r["hazard"] == "heat")
    assert heat["level"] == H.DEATH == 10


def test_parse_damage():
    assert L.parse_damage("10.00K") == 10000 and L.parse_damage("1.5M") == 1.5e6 and L.parse_damage("") == 0


def test_records_to_labels_takes_worst_and_overall():
    recs = pd.DataFrame([
        dict(site_id="gnv", date=dt.date(2024, 6, 5), hazard="storm", level=5, source="NWS", detail=""),
        dict(site_id="gnv", date=dt.date(2024, 6, 5), hazard="storm", level=8, source="LSR", detail=""),
        dict(site_id="gnv", date=dt.date(2024, 6, 5), hazard="heat", level=3, source="NWS", detail=""),
    ])
    lab = L.records_to_labels(recs, [GNV], years=(2024,))
    row = lab[lab["date"] == dt.date(2024, 6, 5)].iloc[0]
    assert row["storm"] == 8 and row["heat"] == 3 and row["overall"] == 8
    assert len(lab) == 366 and lab["overall"].sum() == 8
