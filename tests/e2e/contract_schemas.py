"""
The frontend's data contract (frontend/src/lib/contract.ts), written as small Python schemas
so the tests can check what the backend REALLY sends against what the UI expects.

How to read a schema:
    "str", "num", "int", "bool", "any"   a value of that type ("num" = int or float)
    Opt(x)                              x or null
    OneOf("a", "b")                     one of these exact values
    [x]                                 a list where every item matches x
    DictOf(x)                           an object with any keys, every value matches x
    AnyOf(x, y)                         matches x or y (the UI accepts both shapes)
    {"key": x, "other?": y}             an object; "other?" may be missing. Extra keys are fine
                                        (the UI ignores keys it doesn't know).

Where the UI knowingly accepts a looser shape than contract.ts (frontend/src/lib/normalize.ts
for C1's flat hazards, frontend/src/lib/offset.ts for A's flat offset_update), the schema
accepts it too, and says so.
"""


class Opt:
    def __init__(self, inner):
        self.inner = inner


class OneOf:
    def __init__(self, *values):
        self.values = values


class DictOf:
    def __init__(self, inner):
        self.inner = inner


class AnyOf:
    def __init__(self, *options):
        self.options = options


def check(value, schema, path="$") -> list[str]:
    """Return a list of problems (empty = the value matches the schema)."""
    if isinstance(schema, Opt):
        return [] if value is None else check(value, schema.inner, path)
    if value is None and schema != "any":
        return [f"{path}: is null"]
    if isinstance(schema, AnyOf):
        attempts = [check(value, option, path) for option in schema.options]
        return [] if any(not a for a in attempts) else min(attempts, key=len)
    if isinstance(schema, OneOf):
        return [] if value in schema.values else [f"{path}: {value!r} not one of {list(schema.values)}"]
    if isinstance(schema, list):
        if not isinstance(value, list):
            return [f"{path}: expected a list, got {type(value).__name__}"]
        return [p for i, item in enumerate(value) for p in check(item, schema[0], f"{path}[{i}]")]
    if isinstance(schema, DictOf):
        if not isinstance(value, dict):
            return [f"{path}: expected an object"]
        return [p for k, v in value.items() for p in check(v, schema.inner, f"{path}.{k}")]
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            return [f"{path}: expected an object, got {type(value).__name__}"]
        problems = []
        for key, sub in schema.items():
            optional = key.endswith("?")
            name = key.rstrip("?")
            if name not in value:
                if not optional:
                    problems.append(f"{path}.{name}: missing")
                continue
            problems += check(value[name], sub, f"{path}.{name}")
        return problems
    simple = {"str": str, "bool": bool, "int": int, "num": (int, float)}
    if schema == "any":
        return []
    if schema in ("num", "int") and isinstance(value, bool):
        return [f"{path}: expected {schema}, got bool"]
    if not isinstance(value, simple[schema]):
        return [f"{path}: expected {schema}, got {type(value).__name__} {value!r}"[:200]]
    return []


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
STATUS = OneOf("green", "yellow", "red")
ALERT_KIND = OneOf("HEAT_YELLOW", "HEAT_RED", "LIGHTNING", "FLOOD_FIRE", "MANDOWN", "SOS")
BUZZ = OneOf("HEAT_YELLOW", "HEAT_RED", "LIGHTNING", "FLOOD_FIRE", "MANDOWN", "OFF")
HAZARD_KIND = OneOf("heat", "lightning", "flood", "fire", "soil", "wildlife")
HAZARD_LEVEL = OneOf("none", "low", "moderate", "high", "extreme")
VIEW = OneOf("crew", "worker", "demo")
SITE_TECH = OneOf("gas", "nuclear", "solar", "coal", "oil", "biomass")
EMERGENCY_STAGE = OneOf("notifying", "cancel_window", "calling", "connected", "done", "cancelled", "failed")
MODE = OneOf("dry_run", "fake", "live")
TASK = OneOf("heavy", "moderate", "light", "break", "stop_work", "off")
REPORT_KIND = OneOf("snake", "alligator", "fire_ants", "standing_water", "downed_line", "other")

# ---------------------------------------------------------------------------
# Sites and hazards
# ---------------------------------------------------------------------------
SITE = {"id": "str", "name": "str", "operator": "str", "tech": "str",   # tech values: see SITE_TECH test
        "capacity_mw": "num", "lat": "num", "lon": "num", "county": "str", "coastal": "bool"}
STRIKE = {"lat": "num", "lon": "num", "at": "num", "miles": "num"}
LIGHTNING_INFO = {"nearest_miles": Opt("num"), "last_strike_at": Opt("num"), "all_clear_at": Opt("num"),
                  "strikes": [STRIKE]}
HAZARD = {
    "kind": HAZARD_KIND, "level": HAZARD_LEVEL,
    # C1 sends null headline/detail/source/updated_at on quiet rows; normalize.ts fills them in.
    "headline": Opt("str"), "detail": Opt("str"), "source": Opt("str"), "updated_at": Opt("num"),
    "stop_work": "bool", "band_pattern": Opt(BUZZ),
    "stale?": "bool", "fake?": "bool",
    "lightning?": LIGHTNING_INFO, "heat?": {"wbgt_c": "num", "heat_index_f": "num"},
    # C1's flat versions of the two blocks above (normalize.ts accepts both):
    "nearest_miles?": Opt("num"), "last_strike_at?": Opt("num"), "all_clear_at?": Opt("num"),
    "strikes?": [STRIKE], "wbgt_c?": Opt("num"), "heat_index_f?": Opt("num"),
}
HAZARD_INDEX = {"site_id": "str", "date": "str", "index": "num", "band": OneOf("low", "moderate", "high", "extreme"),
                "climatology_index": "num", "p_advisory": "num", "p_warning": "num", "p_damage": "num",
                "hazards": DictOf("any"), "top_reasons": [{"hazard": "str", "text": "str"}],
                "cutoff": "str", "data_mode": OneOf("archive", "live", "mock"), "model_version": "str",
                "notes": ["str"]}
SITE_STATUS = {"site_id": "str", "hazards": [HAZARD], "index_tomorrow": Opt("num"), "index_drivers": ["str"],
               "hazard_index?": Opt(HAZARD_INDEX), "worker_flag": OneOf("none", "heat", "mandown", "sos"),
               "workers_on_site": "int", "updated_at": "num"}

# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------
WORKER_STATUS = {"worker_id": "str", "name": "str", "role": "str", "site_id": "str", "status": STATUS,
                 "minutes_to_danger": Opt("num"), "status_reason": "str", "hazard": Opt(ALERT_KIND),
                 "alert_acknowledged": Opt("bool"), "on_break": "bool", "break_at": Opt("num"),
                 "band_connected": "bool", "updated_at": "num"}
VITALS = {"worker_id": "str", "timestamp": "num", "hr": Opt("num"), "skin_temp": Opt("num"),
          "activity": Opt("num"), "tilt": Opt("num")}
FORECAST_POINT = {"minutes_ahead": "num", "p10": "num", "p50": "num", "p90": "num"}
ESTIMATE = {"worker_id": "str", "timestamp": "num", "core": {"mean": "num", "p10": "num", "p90": "num"},
            "kalman": "num", "forecast": [FORECAST_POINT], "why": Opt("str"), "offset_applied_c?": "num",
            "thresholds": {"warning": "num", "danger": "num", "demo": "bool"}, "model": "str"}

# ---------------------------------------------------------------------------
# Alerts, man-down, SOS, emergency
# ---------------------------------------------------------------------------
ALERT = {"id": "str", "kind": ALERT_KIND, "worker_id": Opt("str"), "site_id": Opt("str"), "started_at": "num",
         "message": "str", "source": "str", "acknowledged_at": Opt("num"), "cleared_at": Opt("num")}
MANDOWN = {"id": "str", "worker_id": "str", "worker_name": "str", "site_id": "str", "site_name": "str",
           "lat": "num", "lon": "num", "fell_at": "num", "check_started_at": "num", "confirm_at": "num",
           "confirmed_at": Opt("num"), "stage": OneOf("checking", "confirmed", "cancelled", "resolved"),
           "reason": OneOf("fall", "no_response_to_red", "demo", "sos"),
           "last_vitals": {"core_temp": Opt("num"), "hr": Opt("num"), "at": "num"}}
SOS_PRESS = {"worker_id": "str", "worker_name": "str", "site_id": "str", "site_name": "str", "lat": "num",
             "lon": "num", "pressed_at": "num", "source": OneOf("band", "phone", "demo"), "escalated": "bool"}
EMERGENCY_CALL = {"id": "str", "mandown_id": "str", "worker_id": "str", "stage": EMERGENCY_STAGE,
                  "started_at": "num", "cancel_deadline": Opt("num"), "demo": "bool", "target_label": "str",
                  "steps": [{"at": "num", "stage": EMERGENCY_STAGE, "text": "str"}],
                  "transcript": [{"at": "num", "speaker": OneOf("agent", "dispatcher"), "text": "str"}],
                  "outcome": Opt("str"), "mode?": MODE}
EMERGENCY_STATUS = {"incident_id": "str", "worker_id": "str", "worker_name?": Opt("str"), "timestamp": "num",
                    "stage": OneOf("notify", "cancel_window", "emergency_call", "done"),
                    "status": OneOf("calling", "connected", "completed", "failed", "cancelled", "waiting"),
                    "target": Opt(OneOf("crew_lead", "safety_officer", "emergency")),
                    "number_masked?": Opt("str"), "mode?": MODE, "demo_label?": Opt("str"),
                    "cancel_deadline?": Opt("num"), "message?": "str"}

# ---------------------------------------------------------------------------
# Demo, band, reports, planner, offsets
# ---------------------------------------------------------------------------
DEMO_STATE = {"source": OneOf("live", "replay"), "speed": "num", "demo_threshold": "bool",
              "thresholds": {"warning": "num", "danger": "num"},
              "scenario": {"running": "bool", "step": Opt("str"), "started_at": Opt("num")}, "mock": "bool"}
BAND_COMMAND = {"worker_id": "str", "command": "str", "at": "num"}
PHOTO_REPORT = {"id": "str", "site_id": "str", "worker_id": Opt("str"), "kind": REPORT_KIND, "note": "str",
                "lat": "num", "lon": "num", "photo": Opt("str"), "at": "num"}
LOG_ENTRY = {"at": "num", "text": "str", "source": "str"}
SCHEDULE = {"date": "str", "slot_minutes": "num", "day_start": "num",
            "rows": [{"worker_id": "str", "name": "str", "slots": [{"start": "num", "task": TASK, "label": "str"}]}],
            "notes": ["str"], "checked_by_rules": "bool"}
BRIEFING = {"generated_at": "num", "for_date": "str", "summary": "str",
            "sites": [{"site_id": "str", "index": "num",
                       "top_hazards": [{"kind": "str", "level": HAZARD_LEVEL, "text": "str"}],
                       "windows": [{"kind": "str", "start_hour": "num", "end_hour": "num", "text": "str"}],
                       "past_incidents": [{"date": "str", "text": "str", "source": "str"}]}],
            "schedule_suggestions": ["str"]}
OFFSET_UPDATE = {"worker_id": "str", "timestamp": "num", "offset_c": "num", "confidence": "num", "checks": "num",
                 "last_check": Opt({"thermometer_c": "num", "model_estimate_c": "num", "miss_c": "num",
                                    "accepted": "bool", "at": "num"}),
                 "history": [{"timestamp": "num", "offset_c": "num", "confidence": "num", "checks": "num"}]}
# Session A's flat offset shape. frontend/src/lib/offset.ts (toOffsetUpdate) accepts it on the live event
# and on the POST /spot-check reply.
OFFSET_FLAT = {"worker_id": "str", "offset_c": "num", "timestamp?": Opt("num"), "confidence?": "num",
               "n_checks?": "num", "accepted?": "bool", "thermometer_c?": "num", "model_c?": "num",
               "miss_c?": "num", "reason?": Opt("str")}
SPOT_CHECK_RESULT = {"ok": "bool", "accepted": "bool", "message": "str", "offset": Opt(OFFSET_UPDATE)}

SNAPSHOT = {"type": OneOf("snapshot"), "view": VIEW, "sim_time": "num", "workers": [WORKER_STATUS],
            "sites": [SITE_STATUS], "alerts": [ALERT], "mandown": Opt(MANDOWN), "emergency": Opt(EMERGENCY_CALL),
            "demo": DEMO_STATE, "reports": [PHOTO_REPORT], "log": [LOG_ENTRY],
            "history": DictOf({"vitals": [VITALS], "estimates": [ESTIMATE]}), "band": [BAND_COMMAND],
            "offsets?": DictOf("any")}        # checked against OFFSET_UPDATE in its own test

# Every live message type in contract.ts (LiveEvent), and its shape.
LIVE_EVENTS = {
    "snapshot": SNAPSHOT,
    "clock": {"sim_time": "num", "speed": "num"},
    "worker_status": {"worker": WORKER_STATUS},
    "vitals": {"vitals": VITALS},
    "estimate": {"estimate": ESTIMATE},
    "alert": {"alert": ALERT},
    "mandown": {"mandown": Opt(MANDOWN)},
    "sos": {"sos": SOS_PRESS},
    "emergency": {"emergency": Opt(EMERGENCY_CALL)},
    "emergency_status": EMERGENCY_STATUS,
    "site_status": {"site": SITE_STATUS},
    "demo": {"demo": DEMO_STATE},
    "band_command": {"band": BAND_COMMAND},
    "report": {"report": PHOTO_REPORT},
    "log": {"entry": LOG_ENTRY},
    "offset_update": AnyOf({"offset": OFFSET_UPDATE}, OFFSET_FLAT),
}

OK = {"ok": "bool"}
# REST replies the UI reads (frontend-contract.md "REST" + "Demo controls").
REST = {
    "GET /api/state": SNAPSHOT,
    "GET /api/workers": [WORKER_STATUS],
    "GET /api/workers/{id}/history": {"vitals": [VITALS], "estimates": [ESTIMATE]},
    "GET /api/sites": [SITE],
    "GET /api/schedule": SCHEDULE,
    "GET /api/reports": [PHOTO_REPORT],
    "POST /api/reports": {"ok": "bool", "report": PHOTO_REPORT},
    "POST /api/workers/{id}/button": {"ok": "bool", "action": "str"},
    "POST /api/workers/{id}/sos": {"ok": "bool", "action": OneOf("escalated", "ignored_active_incident")},
    "POST /api/alerts/{id}/ack": OK,
    "POST /api/mandown/{id}/resolve": OK,
    "POST /api/emergency/cancel": AnyOf(OK, {"ok": OneOf(False), "error": "str"}),
    "POST /api/emergency/{id}/cancel": OK,
    "POST /api/spot-check": AnyOf(SPOT_CHECK_RESULT, OFFSET_FLAT),    # LearningCard reads both
    "POST /api/demo/threshold": DEMO_STATE,
    "POST /api/demo/replay": DEMO_STATE,
    "POST /api/demo/test-alert": OK,
    "POST /api/demo/clear-hazards": OK,
    "POST /api/demo/mandown": OK,
    "POST /api/demo/sos": OK,
}
