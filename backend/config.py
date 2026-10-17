"""
All the numbers the system runs on live here, in one place.

SAFETY LIMITS ARE HARD-CODED. Nothing in this project is allowed to learn,
tune, or overwrite them at runtime. If you want to change one, change it here,
on purpose, and tell the team.
"""

import os

from dotenv import load_dotenv

# Read secrets and site settings from the .env file (see .env.example).
load_dotenv()


# ---------------------------------------------------------------------------
# Kalman filter: ECTemp (Buller et al., 2013)
# Core temperature is hidden; 1-minute average heart rate is a noisy view of it.
# GAMMA and SIGMA are standard deviations. The equations use their squares.
# Checked against US patent 10,702,165 (equations, sigma, b0-b2, v0 = 0).
# ---------------------------------------------------------------------------
KALMAN_GAMMA = 0.022      # how much core temp can drift per minute (°C)
KALMAN_SIGMA = 18.88      # how noisy heart rate is as a reading of core temp (bpm)
KALMAN_B2 = -4.5714       # heart rate model: HR = b2*CT^2 + b1*CT + b0
KALMAN_B1 = 384.4286
KALMAN_B0 = -7887.1
KALMAN_CT_START = 37.1    # starting core temp (°C)
KALMAN_V_START = 0.0      # starting uncertainty (variance), per Buller 2013


# ---------------------------------------------------------------------------
# Heat alert thresholds (Alerts table in PROJECT.md). °C.
# ---------------------------------------------------------------------------
HEAT_WARNING_C = 38.0            # yellow: 20-min forecast (90th pct) crosses this
HEAT_DANGER_C = 38.5             # red: current estimate or 10-min forecast crosses this
HEAT_WARNING_HORIZON_MIN = 20    # which forecast horizon drives yellow
HEAT_DANGER_HORIZON_MIN = 10     # which forecast horizon drives red
HEAT_ALERT_QUANTILE = 0.9        # alerts use the cautious 90th percentile

# Demo thresholds: real core temp takes 15-20 min of hard work to hit 38.5,
# too slow for judges. These are clearly labeled on the dashboard when on.
DEMO_HEAT_WARNING_C = 37.3
DEMO_HEAT_DANGER_C = 37.5

# An alert only clears once the temp drops this far below its threshold,
# so it doesn't flicker on and off when the temp hovers right at the line.
ALERT_CLEAR_MARGIN_C = 0.1

# Red alert: band repeats the long buzz this often until the button is pressed.
HEAT_RED_REPEAT_S = 10
# Red alert with no button press for this long also triggers a man-down check.
HEAT_RED_ACK_TIMEOUT_S = 60


# ---------------------------------------------------------------------------
# Man-down (used in Phase 9)
# ---------------------------------------------------------------------------
MANDOWN_STILL_S = 45         # tipped over + no movement this long -> check (spec: 30-60 s)
MANDOWN_ESCALATE_S = 60      # not cancelled this long -> alert crew lead and crewmates


# ---------------------------------------------------------------------------
# Forecaster outputs (used in Phase 3)
# ---------------------------------------------------------------------------
FORECAST_HORIZONS_MIN = (10, 20, 30)
FORECAST_QUANTILES = (0.1, 0.5, 0.9)


# ---------------------------------------------------------------------------
# Band commands (line protocol, laptop -> band). Sent as "BUZZ:<PATTERN>".
# ---------------------------------------------------------------------------
BUZZ_PATTERNS = ("HEAT_YELLOW", "HEAT_RED", "LIGHTNING", "FLOOD_FIRE", "MANDOWN", "OFF")


# ---------------------------------------------------------------------------
# Data and transport
# ---------------------------------------------------------------------------
FEATURE_PERIOD_S = 60        # models get 1-minute averages
MAX_GAP_MIN = 10             # fill at most this many empty minutes after a data gap
SERIAL_BAUD = 115200
UDP_PORT = int(os.getenv("UDP_PORT", "5005"))
PULSOID_WS_URL = "wss://dev.pulsoid.net/api/v1/data/real_time"
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

# Secrets and site settings from .env
PULSOID_TOKEN = os.getenv("PULSOID_TOKEN", "")
SERIAL_PORT = os.getenv("SERIAL_PORT", "")
WORKER_ID = os.getenv("WORKER_ID", "worker-1")
JOB_SITE_LAT = float(os.getenv("JOB_SITE_LAT", "29.6516"))   # Gainesville, FL
JOB_SITE_LON = float(os.getenv("JOB_SITE_LON", "-82.3248"))
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "planit-control (contact@example.com)")

# Where things get saved
DB_PATH = os.getenv("DB_PATH", "data/planit.db")
RECORDINGS_DIR = "data/recordings"


# ===========================================================================
# SITE HAZARD LAYER (session C1: backend/sites, backend/hazards)
# Safety limits for job-site hazards. Hard-coded on purpose, never learned.
# ===========================================================================

# Lightning stop-work rule (GOES-19 GLM flashes)
LIGHTNING_STOP_RADIUS_MI = 10        # good-quality flash this close -> stop work
LIGHTNING_ALL_CLEAR_MIN = 30         # back to work only after this long with no flash; resets on each new one
LIGHTNING_MIN_FLASHES = 2            # need this many flashes before stopping (GOES-19 false flash problem)
LIGHTNING_CONFIRM_WINDOW_MIN = 30    # ...within this many minutes of each other
LIGHTNING_GOOD_QUALITY_FLAG = 0      # only keep flashes with flash_quality_flag == this
LIGHTNING_WATCH_RADIUS_MI = 30       # flashes this close show "lightning nearby" (no stop)
LIGHTNING_FEED_STALE_MIN = 5         # no fresh GLM data this long -> never give the all-clear

# WBGT heat flags (°C), from the standard 80/85/88/90 °F flag system
WBGT_GREEN_C = 26.7                  # 80 °F: heat is a factor, watch new workers
WBGT_YELLOW_C = 29.4                 # 85 °F: more breaks
WBGT_RED_C = 31.1                    # 88 °F: limit heavy work
WBGT_BLACK_C = 32.2                  # 90 °F: extreme, stop strenuous outdoor work

# Flooding
RIVER_GAUGE_MAX_DIST_MI = 15         # ignore river gauges farther than this from a site
TIDE_STATION_MAX_DIST_MI = 12        # sites within this of a tide station count as coastal
FLOOD_FORECAST_HOURS = 48            # look this far ahead in river forecasts

# Wildfire
FIRE_DETECTION_WARNING_MI = 5        # satellite fire detection this close -> warning
FIRE_DETECTION_ADVISORY_MI = 15      # ...this close -> advisory
FIRE_INCIDENT_WARNING_MI = 10        # active NIFC wildfire this close -> warning

# Soil saturation (Open-Meteo top-soil moisture, m³/m³; Florida sand saturates near 0.4)
SOIL_WET_M3M3 = 0.30                 # advisory: soft ground, standing water likely
SOIL_SATURATED_M3M3 = 0.38           # warning: saturated, flooding and equipment risk
HEAVY_RAIN_24H_MM = 50               # this much rain in 24 h -> flooding advisory ("night of heavy rain")

# Wildlife (iNaturalist sightings)
WILDLIFE_RADIUS_MI = 5               # count sightings within this radius
WILDLIFE_DAYS = 7                    # ...in the last this many days
