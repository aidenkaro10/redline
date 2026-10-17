"""Every planner test runs offline: no Claude calls, no storm forecast downloads, no real texts."""

import os

os.environ["PLANNER_LLM"] = "off"
os.environ["PLANNER_STORM_MODE"] = "off"
os.environ["PLANNER_SMS_MODE"] = "dry_run"
os.environ["PLANNER_SOLVER_WORKERS"] = "2"

# Test feed / SMS logs go to a temp folder, not data/planner/.
import tempfile

_tmp = tempfile.mkdtemp(prefix="planner-tests-")
os.environ["PLANNER_FEED_PATH"] = os.path.join(_tmp, "feed.jsonl")
os.environ["PLANNER_SMS_LOG_PATH"] = os.path.join(_tmp, "sms_log.jsonl")
