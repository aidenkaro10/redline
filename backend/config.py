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
