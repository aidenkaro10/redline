"""Every planner test runs offline: no Claude calls, no storm forecast downloads, no real texts."""

import os

os.environ["PLANNER_LLM"] = "off"
os.environ["PLANNER_STORM_MODE"] = "off"
os.environ["PLANNER_SMS_MODE"] = "dry_run"
