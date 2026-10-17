"""
SQLite storage: one file (data/planit.db), no account, works with no WiFi.

Tables
    workers    one row per worker (name, site)
    sites      job sites (session C1 adds the 50+ power plants)
    readings   every raw reading, laptop timestamp
    alerts     every alert-type event: heat alerts, acks, band commands,
               man-down, hazards (full event as JSON too)
    plans      schedules from the planner (session B)
    reports    crew photo reports (snake, standing water, downed line ...)
    offsets    each worker's personal offset twin (thermometer spot-check misses)
    spot_checks every thermometer spot check, kept or rejected
    estimates  one row per worker per minute: inputs (per heart rate device too), Kalman, model estimate,
               forecast at +10/+20/+30 min, model name, twin version.
               The twins learn from this table later.

Writes are committed about once a second, not one by one, so a replay
burst of thousands of readings stays fast.
"""

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS workers (
    worker_id TEXT PRIMARY KEY, name TEXT, site_id TEXT, source TEXT, updated_at REAL
);
CREATE TABLE IF NOT EXISTS sites (
    site_id TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL
);
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY, worker_id TEXT, timestamp REAL,
    hr REAL, skin_temp REAL, tilt INTEGER, activity REAL, ax REAL, ay REAL, az REAL
);
CREATE INDEX IF NOT EXISTS readings_worker_time ON readings (worker_id, timestamp);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY, worker_id TEXT, site_id TEXT, timestamp REAL,
    type TEXT, detail TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS alerts_worker_time ON alerts (worker_id, timestamp);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY, timestamp REAL, source TEXT, plan TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, site_id TEXT, worker_id TEXT, kind TEXT, note TEXT,
    lat REAL, lon REAL, photo TEXT, at REAL
);
CREATE TABLE IF NOT EXISTS offsets (
    worker_id TEXT PRIMARY KEY, misses TEXT, offset_c REAL, confidence REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS spot_checks (
    id INTEGER PRIMARY KEY, worker_id TEXT, timestamp REAL, thermometer_c REAL, model_c REAL,
    accepted INTEGER, offset_c REAL
);
CREATE TABLE IF NOT EXISTS estimates (
    worker_id TEXT, timestamp REAL, minute INTEGER,
    hr REAL, skin_temp REAL, activity REAL, wbgt REAL,
    kalman REAL, kalman_sd REAL, core_temp REAL, core_temp_sd REAL,
    f10_p10 REAL, f10_p50 REAL, f10_p90 REAL,
    f20_p10 REAL, f20_p50 REAL, f20_p90 REAL,
    f30_p10 REAL, f30_p50 REAL, f30_p90 REAL,
    heat_level TEXT, model TEXT, twin_version TEXT,
    PRIMARY KEY (worker_id, timestamp)
);
"""

# Event types saved in the alerts table, and which field is the short "detail".
ALERT_EVENTS = {
    "heat_alert": "level", "heat_ack": None, "red_unacknowledged": None,
    "band_command": "pattern", "hazard": "kind",
    "mandown_check": "reason", "mandown_cancelled": "by", "mandown_confirmed": "reason",
    "mandown_resolved": "by", "mandown_worker_responded": "by", "sos": None,
    "emergency_status": "status", "offset_update": "offset_c",
}
COMMIT_EVERY_S = 1.0

# Columns added after the first version. Older database files get them added on open.
NEW_COLUMNS = {
    "readings": [("source", "TEXT")],                                        # watch / band / replay / demo
    "estimates": [("hr_watch", "REAL"), ("hr_band", "REAL"), ("hr_sources", "TEXT")],   # which HR fed the Kalman
}
ESTIMATE_COLUMNS = (
    "worker_id", "timestamp", "minute", "hr", "skin_temp", "activity", "wbgt",
    "kalman", "kalman_sd", "core_temp", "core_temp_sd",
    "f10_p10", "f10_p50", "f10_p90", "f20_p10", "f20_p50", "f20_p90", "f30_p10", "f30_p50", "f30_p90",
    "heat_level", "model", "twin_version", "hr_watch", "hr_band", "hr_sources",
)


class Database:
    def __init__(self, path: str | Path = "data/planit.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the serial reader thread hands readings to the
        # event loop, but every write still happens on the loop's thread.
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        for table, cols in NEW_COLUMNS.items():
            have = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, kind in cols:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        self._last_commit = time.monotonic()

    # -- writes ---------------------------------------------------------------
    def _write(self, sql: str, args: tuple) -> None:
        self.conn.execute(sql, args)
        if time.monotonic() - self._last_commit > COMMIT_EVERY_S:
            self.commit()

    def commit(self) -> None:
        self.conn.commit()
        self._last_commit = time.monotonic()

    def upsert_worker(self, info: dict) -> None:
        self._write("INSERT OR REPLACE INTO workers VALUES (?, ?, ?, ?, ?)",
                    (info["worker_id"], info.get("name"), info.get("site_id"), info.get("source"), time.time()))

    def upsert_site(self, site: dict) -> None:
        self._write("INSERT OR REPLACE INTO sites VALUES (?, ?, ?, ?)",
                    (site["site_id"], site.get("name"), site.get("lat"), site.get("lon")))

    def add_reading(self, r) -> None:
        ax, ay, az = r.accel if r.accel else (None, None, None)
        self._write("INSERT INTO readings (worker_id, timestamp, hr, skin_temp, tilt, activity, ax, ay, az, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r.worker_id, r.timestamp, r.hr, r.skin_temp, r.tilt, r.activity, ax, ay, az, r.source))

    def add_plan(self, ev: dict) -> None:
        self._write("INSERT INTO plans (timestamp, source, plan) VALUES (?, ?, ?)",
                    (ev["timestamp"], ev.get("source"), json.dumps(ev["plan"])))

    def save_offset(self, d: dict) -> None:
        """d = PersonalOffset.to_dict(). Committed right away: it's precious and rare."""
        self._write("INSERT OR REPLACE INTO offsets VALUES (?, ?, ?, ?, ?)",
                    (d["worker_id"], json.dumps(d["misses"]), d["offset_c"], d["confidence"], time.time()))
        self.commit()

    def load_offset(self, worker_id: str) -> dict | None:
        rows = self.query("SELECT misses FROM offsets WHERE worker_id = ?", (worker_id,))
        return {"worker_id": worker_id, "misses": json.loads(rows[0][0])} if rows else None

    def add_spot_check(self, ev: dict) -> None:
        self._write("INSERT INTO spot_checks (worker_id, timestamp, thermometer_c, model_c, accepted, offset_c) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ev["worker_id"], ev["timestamp"], ev.get("thermometer_c"), ev.get("model_c"),
                     int(bool(ev.get("accepted"))), ev.get("offset_c")))

    def add_report(self, r: dict) -> None:
        self._write("INSERT OR REPLACE INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["id"], r["site_id"], r.get("worker_id"), r["kind"], r["note"],
                     r.get("lat"), r.get("lon"), r.get("photo"), r["at"]))

    def log_event(self, ev: dict) -> None:
        """Called by the hub for every event; keeps the ones that belong in a table."""
        kind = ev["type"]
        if kind == "estimate":
            self._add_estimate(ev)
        elif kind in ALERT_EVENTS:
            field = ALERT_EVENTS[kind]
            self._write("INSERT INTO alerts (worker_id, site_id, timestamp, type, detail, payload) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (ev.get("worker_id"), ev.get("site_id"), ev["timestamp"], kind,
                         str(ev.get(field)) if field else None, json.dumps(ev)))

    def _add_estimate(self, ev: dict) -> None:
        fc = ev.get("forecast")
        horizons = []
        for h in (10, 20, 30):
            for q in ("p10", "p50", "p90"):
                horizons.append(fc[q][h - 1] if fc else None)
        values = (
            ev["worker_id"], ev["timestamp"], ev["minute"],
            ev.get("hr"), ev.get("skin_temp"), ev.get("activity"), ev.get("wbgt"),
            ev.get("kalman"), ev.get("kalman_sd"), ev.get("core_temp"), ev.get("core_temp_sd"),
            *horizons, ev.get("heat_level"), ev.get("model"), ev.get("twin_version"),
            ev.get("hr_watch"), ev.get("hr_band"), ",".join(ev.get("hr_sources") or []))
        self._write(f"INSERT OR REPLACE INTO estimates ({', '.join(ESTIMATE_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(ESTIMATE_COLUMNS))})", values)

    # -- reads ----------------------------------------------------------------
    def query(self, sql: str, args: tuple = ()) -> list[tuple]:
        return self.conn.execute(sql, args).fetchall()

    def close(self) -> None:
        self.commit()
        self.conn.close()
