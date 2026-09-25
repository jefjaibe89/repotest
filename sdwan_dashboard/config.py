import os

from dotenv import load_dotenv

load_dotenv()

VMANAGE_HOST = os.getenv("VMANAGE_HOST", "localhost")
VMANAGE_PORT = int(os.getenv("VMANAGE_PORT", "8443"))
VMANAGE_USER = os.getenv("VMANAGE_USER", "admin")
VMANAGE_PASS = os.getenv("VMANAGE_PASS", "admin")
VMANAGE_VERIFY_SSL = os.getenv("VMANAGE_VERIFY_SSL", "false").lower() == "true"
VMANAGE_TIMEOUT = int(os.getenv("VMANAGE_TIMEOUT", "20"))

# Set to "mock" to use demo data without a real vManage instance
MODE = os.getenv("SDWAN_MODE", "mock")

FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "30"))

# How long a logged-in vManage session is reused before re-authenticating.
# vManage sessions idle out around 30 min by default; stay well under it.
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "900"))

# Thresholds that drive the health score and the CPU/memory colour bars.
CPU_WARN = int(os.getenv("CPU_WARN", "65"))
CPU_CRIT = int(os.getenv("CPU_CRIT", "85"))
MEM_WARN = int(os.getenv("MEM_WARN", "65"))
MEM_CRIT = int(os.getenv("MEM_CRIT", "85"))

ALARM_WINDOW_HOURS = int(os.getenv("ALARM_WINDOW_HOURS", "24"))
