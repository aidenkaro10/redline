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
# Sensor fusion (Kalman.step_fused): how noisy each heart rate source is, in bpm.
# The watch uses the published sigma. A pulse sensor on the band is noisier while
# moving, so it counts less. Provisional until we test the real band sensor.
KALMAN_SIGMA_WATCH = KALMAN_SIGMA
KALMAN_SIGMA_BAND_PPG = 2 * KALMAN_SIGMA


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
WILDLIFE_CLOSE_MI = 1                # alligator or venomous snake this close -> advisory

# ===========================================================================
# Emergency escalation (session D, backend/emergency/)
# Confirmed man-down -> call crew lead + safety officer -> cancel window ->
# emergency call. The prototype NEVER dials 911. The emergency leg goes to a
# teammate's test phone and the screen says "Demo: would call 911".
# ===========================================================================
# How long the crew lead has to press "cancel, false alarm" after being
# notified, before the emergency call goes out.
EMERGENCY_CANCEL_WINDOW_S = 30
# Shorter window for live demos, so judges aren't waiting. Labeled on screen.
EMERGENCY_DEMO_CANCEL_WINDOW_S = 10

# Numbers the code refuses to dial, no matter what .env says.
# 911 (US), 112 (EU/mobile), 999 (UK), 000 (AU), 111 (NZ), 110/119 (JP/CN),
# 988 (US crisis line), 933 (US 911 address test line).
EMERGENCY_BLOCKED_NUMBERS = ("911", "112", "999", "000", "111", "110", "119", "988", "933")
# Real phone numbers have at least this many digits. Anything shorter is a
# short code (like 911) and is refused.
EMERGENCY_MIN_DIGITS = 10

# Text shown on the dashboard for the emergency leg in the prototype.
EMERGENCY_DEMO_LABEL = "Demo: would call 911"

# Live calling is OFF unless .env says EMERGENCY_LIVE_CALLS=1.
# Off = dry run: the call script is printed and logged, no phone rings.
EMERGENCY_LIVE_CALLS = os.getenv("EMERGENCY_LIVE_CALLS", "0") == "1"

# The only numbers the system may ever call (the allowlist). From .env.
# Use full international format, e.g. +13525551234.
CREW_LEAD_NUMBER = os.getenv("CREW_LEAD_NUMBER", "")
SAFETY_OFFICER_NUMBER = os.getenv("SAFETY_OFFICER_NUMBER", "")
EMERGENCY_TEST_NUMBER = os.getenv("EMERGENCY_TEST_NUMBER", "")  # teammate playing dispatcher

# Every escalation step is appended here, one JSON object per line.
EMERGENCY_LOG_PATH = os.getenv("EMERGENCY_LOG_PATH", "data/emergency_log.jsonl")

# ElevenLabs voice agent (only used when EMERGENCY_LIVE_CALLS=1). From .env.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_PHONE_NUMBER_ID", "")

# ===========================================================================
# ---------------------------------------------------------------------------
# PLANNER SAFETY LIMITS (session B, backend/planner/). Hard-coded, never learned.
# The rule checker enforces these on every schedule. No learning loop, prompt,
# or LLM can change them.
# ---------------------------------------------------------------------------
PLAN_SLOT_MIN = 30                        # schedules use 30-minute slots
PLAN_MAX_P90_C = HEAT_WARNING_C           # planned 90th pct core temp must stay BELOW the yellow line, every minute
PLAN_MAX_CONSECUTIVE_WORK_SLOTS = 3       # at most 90 min of work before a 30-min shade break
PLAN_MAX_CONSECUTIVE_WORK_SLOTS_HOT = 2   # ...or 60 min when the slot WBGT is at/above PLAN_HOT_WBGT_C
PLAN_HOT_WBGT_C = 30.0                    # "hot" slot for the break rule (°C WBGT)
PLAN_LIGHTNING_RADIUS_MI = 10             # lightning within this many miles = stop outdoor work
PLAN_LIGHTNING_ALLCLEAR_MIN = 30          # outdoor work resumes only 30 min after the last strike
PLAN_UNACCLIM_LEVEL = 0.5                 # acclimatization below this = not acclimatized (new hire)
PLAN_UNACCLIM_NO_VERY_HEAVY_WBGT_C = 28.0 # no very heavy work for unacclimatized workers at/above this WBGT
# NWS hazard kinds that stop ALL outdoor work during their window.
PLAN_STOP_WORK_HAZARDS = ("lightning", "severe_thunderstorm", "tornado", "flash_flood", "flood",
                          "hurricane", "tropical_storm")
# Personal offsets (thermometer spot checks, backend/models/personal_offset.py) in planning:
PLAN_MAX_COOLER_OFFSET_C = 0.3   # a worker can count as at most this much COOLER than the model (hotter: no cap here)
PLAN_PERSON_SD_C = 0.15          # how unsure we are about a person with no spot checks yet (°C)
PLAN_MIN_PERSON_SD_C = 0.05      # never more certain than this, however many checks
