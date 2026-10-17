"""
Which hazards the index scores, and how bad each kind of record is (0-10).

The 0-10 ladder (from docs/data-sources.md, "Training the 0-10 hazard index"):
    0   nothing on record
    1   watch            (NWS says conditions could develop)
    3   advisory         (NWS heat advisory, flood advisory, ...) or a minor report
    5   warning          (NWS warning covered the site)
    6   report           (a storm report near the site: hail, severe gust, ...)
    8   damage           (a report or Storm Event with damage near the site)
    9   injury           (Storm Event injury, or an OSHA heat injury nearby)
   10   death            (Storm Event death)

A day's score for one hazard is the worst record that day. The overall score is
the worst hazard. These are LABEL definitions for training, not safety limits:
the index ranks sites and feeds the 6 PM briefing. It never decides an alert.
"""

HAZARDS = ("heat", "storm", "flood", "fire", "tropical")

NONE, WATCH, ADVISORY, WARNING, REPORT, DAMAGE, INJURY, DEATH = 0, 1, 3, 5, 6, 8, 9, 10

# ---------------------------------------------------------------------------
# NWS VTEC phenomena codes -> our hazard. (Codes: https://www.weather.gov/vtec/)
# NWS renamed Excessive Heat (EH) to Extreme Heat (XH) in March 2025: both map to heat.
# ---------------------------------------------------------------------------
VTEC_HAZARD = {
    "HT": "heat", "EH": "heat", "XH": "heat",
    "SV": "storm", "TO": "storm", "EW": "storm", "WI": "storm", "HW": "storm",
    "FF": "flood", "FA": "flood", "FL": "flood", "CF": "flood",
    "FW": "fire",
    "TR": "tropical", "HU": "tropical", "SS": "tropical", "TY": "tropical",
    "HI": "tropical", "TI": "tropical",
}

# VTEC significance letter -> level. S (statement) and others are ignored.
VTEC_LEVEL = {"A": WATCH, "Y": ADVISORY, "W": WARNING}

# ---------------------------------------------------------------------------
# Local Storm Report type -> (hazard, level). Anything else is ignored.
# ---------------------------------------------------------------------------
LSR_TYPE = {
    "TSTM WND DMG": ("storm", DAMAGE),
    "NON-TSTM WND DMG": ("storm", DAMAGE),
    "TORNADO": ("storm", DAMAGE),
    "LIGHTNING": ("storm", DAMAGE),          # only reported when it hit something
    "TSTM WND GST": ("storm", REPORT),
    "NON-TSTM WND GST": ("storm", REPORT),
    "HAIL": ("storm", REPORT),
    "FUNNEL CLOUD": ("storm", REPORT),
    "FLASH FLOOD": ("flood", DAMAGE),
    "FLOOD": ("flood", REPORT),
    "COASTAL FLOOD": ("flood", REPORT),
    "HEAVY RAIN": ("flood", ADVISORY),       # a rain total, not damage
    "RAIN": ("flood", ADVISORY),
    "WILDFIRE": ("fire", DAMAGE),
    "EXCESSIVE HEAT": ("heat", REPORT),
    "HEAT": ("heat", REPORT),
    "EXTREME HEAT": ("heat", REPORT),
    "TROPICAL CYCLONE": ("tropical", REPORT),   # usually a wind or rain measurement
    "TROPICAL STORM": ("tropical", DAMAGE),
    "HURRICANE": ("tropical", DAMAGE),
    "STORM SURGE": ("tropical", DAMAGE),
}
LSR_RADIUS_MILES = 15        # a storm report counts for a site if it is this close

# ---------------------------------------------------------------------------
# NOAA Storm Events EVENT_TYPE -> hazard.
# ---------------------------------------------------------------------------
STORM_EVENT_HAZARD = {
    "Heat": "heat", "Excessive Heat": "heat",
    "Thunderstorm Wind": "storm", "Hail": "storm", "Tornado": "storm", "Lightning": "storm",
    "Funnel Cloud": "storm", "High Wind": "storm", "Strong Wind": "storm",
    "Flash Flood": "flood", "Flood": "flood", "Heavy Rain": "flood", "Coastal Flood": "flood",
    "Wildfire": "fire",
    "Tropical Storm": "tropical", "Hurricane (Typhoon)": "tropical", "Hurricane": "tropical",
    "Storm Surge/Tide": "tropical", "Tropical Depression": "tropical",
}
STORM_EVENT_RADIUS_KM = 25   # events with coordinates count if this close to a site

# ---------------------------------------------------------------------------
# OSHA Severe Injury Reports (heat exposure is event code 531)
# ---------------------------------------------------------------------------
OSHA_HEAT_CODES = ("531",)
OSHA_FALL_PREFIXES = ("42", "43")             # 42x same level, 43x to a lower level
OSHA_FOCUS_NAICS = ("2211", "237130", "238210")   # utilities, power lines, electricians
OSHA_RADIUS_KM = 40          # an injury counts toward a site if this close


def worst(levels) -> int:
    """The day's score is the worst thing that happened."""
    return max(levels, default=NONE)
