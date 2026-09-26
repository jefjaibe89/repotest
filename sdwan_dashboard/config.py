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
def _verify_setting(raw: str) -> bool | str:
    """Resolve VMANAGE_VERIFY_SSL into what requests expects.

    Accepts true/false, or a path to a CA bundle — vManage usually presents a
    private certificate, so pinning the issuing CA is the answer rather than
    switching verification off. Unrecognised values are treated as a path and
    fail loudly if it does not exist; coercing them to False (as a plain
    boolean flag would) turns a typo, or a bundle path, into silently
    disabled verification.
    """
    value = raw.strip()
    if value.lower() in ("true", "1", "yes", "on"):
        return True
    if value.lower() in ("false", "0", "no", "off"):
        return False
    return value


VMANAGE_VERIFY_SSL = _verify_setting(os.getenv("VMANAGE_VERIFY_SSL", "true"))
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

# ---- Login throttling -----------------------------------------------------
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_SECONDS = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "300"))
# Failures older than this no longer count, so occasional typos never add up.
LOGIN_ATTEMPT_WINDOW_SECONDS = int(os.getenv("LOGIN_ATTEMPT_WINDOW_SECONDS", "900"))
# Only enable behind a proxy you control: it makes the throttle trust
# X-Forwarded-For, which clients can otherwise forge to evade the limit.
TRUST_PROXY_HEADERS = _flag("TRUST_PROXY_HEADERS")

# ---- Prometheus ------------------------------------------------------------
# Off by default: the metrics carry the same network inventory the dashboard
# shows, so exposing them is a deliberate choice.
METRICS_ENABLED = _flag("METRICS_ENABLED")
# A scraper cannot log in through a session, so the login cannot protect this
# endpoint. Set a token and scrape with "Authorization: Bearer <token>".
METRICS_TOKEN = os.getenv("METRICS_TOKEN", "")

# ---- Content Security Policy -----------------------------------------------
# Defence in depth under the output escaping: if a future view introduces an
# unescaped sink, this is what stops the injected script from running.
CSP_ENABLED = _flag("CSP_ENABLED", "true")
