import os
import secrets

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("true", "1", "yes", "on")


# ---- vManage connection ---------------------------------------------------
VMANAGE_HOST = os.getenv("VMANAGE_HOST", "localhost")
VMANAGE_PORT = int(os.getenv("VMANAGE_PORT", "8443"))
VMANAGE_USER = os.getenv("VMANAGE_USER", "admin")
VMANAGE_PASS = os.getenv("VMANAGE_PASS", "admin")
VMANAGE_VERIFY_SSL = _flag("VMANAGE_VERIFY_SSL")
VMANAGE_TIMEOUT = int(os.getenv("VMANAGE_TIMEOUT", "20"))

# Set to "mock" to use demo data without a real vManage instance
MODE = os.getenv("SDWAN_MODE", "mock")

# ---- Web server -----------------------------------------------------------
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = _flag("FLASK_DEBUG")

REFRESH_INTERVAL_SECONDS = int(os.getenv("REFRESH_INTERVAL_SECONDS", "30"))

# ---- Session reuse --------------------------------------------------------
# vManage idles sessions out around 30 min by default; stay well under it.
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "900"))

# ---- Health thresholds ----------------------------------------------------
CPU_WARN = int(os.getenv("CPU_WARN", "65"))
CPU_CRIT = int(os.getenv("CPU_CRIT", "85"))
MEM_WARN = int(os.getenv("MEM_WARN", "65"))
MEM_CRIT = int(os.getenv("MEM_CRIT", "85"))

ALARM_WINDOW_HOURS = int(os.getenv("ALARM_WINDOW_HOURS", "24"))

# ---- Polling and history --------------------------------------------------
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
DB_PATH = os.getenv("DB_PATH", "sdwan_history.db")
POLLER_LOCK_PATH = os.getenv("POLLER_LOCK_PATH", "/tmp/sdwan-dashboard-poller.lock")
HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "7"))
HISTORY_WINDOW_HOURS = int(os.getenv("HISTORY_WINDOW_HOURS", "24"))

# ---- Alerting -------------------------------------------------------------
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
# One of: slack, teams, generic
ALERT_WEBHOOK_FORMAT = os.getenv("ALERT_WEBHOOK_FORMAT", "slack")
ALERT_WEBHOOK_TIMEOUT = int(os.getenv("ALERT_WEBHOOK_TIMEOUT", "10"))
ALERTS_ENABLED = _flag("ALERTS_ENABLED") and bool(ALERT_WEBHOOK_URL)
ALERT_SCORE_THRESHOLD = int(os.getenv("ALERT_SCORE_THRESHOLD", "70"))
ALERT_CRITICAL_ALARM_COUNT = int(os.getenv("ALERT_CRITICAL_ALARM_COUNT", "1"))
# How long before an alert that is still firing is announced again.
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "3600"))

# ---- Dashboard authentication ---------------------------------------------
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
# Unset means no login. auth.warn_if_unprotected() flags that in live mode.
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
# A generated key logs everyone out on restart, which beats shipping a default.
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE")
