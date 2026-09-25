"""
Optional dashboard authentication.

The dashboard exposes the full network inventory — hostnames, system IPs,
models, firmware versions, sites — and the process holds vManage credentials.
Setting DASHBOARD_PASSWORD puts a login in front of all of it. Leaving it unset
keeps the dashboard open, which is fine for a demo but is warned about loudly
at startup in live mode.
"""

import functools
import hmac
import logging
import time

from flask import redirect, request, session, url_for

import config
import store

log = logging.getLogger("sdwan-dashboard.auth")


def client_id() -> str:
    """Identify the caller for throttling purposes.

    X-Forwarded-For is only honoured when the deployment says it sits behind a
    trusted proxy. Trusting it unconditionally would let an attacker send a
    fresh header on every request and never be throttled at all.
    """
    if config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # Left-most entry is the original client; the rest are proxies.
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def lockout_remaining(cid: str) -> int:
    """Seconds left on this client's lockout, or 0 when it may try again."""
    record = store.get_login_attempt(cid)
    if not record or not record.get("locked_until"):
        return 0
    remaining = record["locked_until"] - time.time()
    return max(0, int(remaining))


def register_failure(cid: str) -> int:
    """Record a failed attempt. Returns seconds locked out, 0 if not yet locked."""
    result = store.record_login_failure(
        cid,
        now=time.time(),
        window=config.LOGIN_ATTEMPT_WINDOW_SECONDS,
        max_attempts=config.LOGIN_MAX_ATTEMPTS,
        lockout=config.LOGIN_LOCKOUT_SECONDS,
    )
    if result["locked_until"]:
        log.warning("Locked out %s after %s failed logins", cid, result["failures"])
        return config.LOGIN_LOCKOUT_SECONDS
    return 0


def register_success(cid: str):
    store.clear_login_attempts(cid)


def enabled() -> bool:
    return bool(config.DASHBOARD_PASSWORD)


def check_credentials(username: str, password: str) -> bool:
    # compare_digest on both fields so neither can be probed by timing.
    user_ok = hmac.compare_digest(username or "", config.DASHBOARD_USER)
    pass_ok = hmac.compare_digest(password or "", config.DASHBOARD_PASSWORD)
    return user_ok and pass_ok


def login_required(fn):
    """Gate a view behind the session login, when authentication is configured."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not enabled() or session.get("authenticated"):
            return fn(*args, **kwargs)

        # XHR callers get a 401 to act on; browsers get sent to the login page.
        if request.path.startswith("/api/"):
            return {"error": "auth_required", "message": "Login required"}, 401
        return redirect(url_for("login", next=request.path))

    return wrapper


def warn_if_unprotected():
    if enabled():
        return
    if config.MODE == "live":
        log.warning(
            "DASHBOARD_PASSWORD is not set: the network inventory is served "
            "without authentication. Set it, or restrict access at the network layer."
        )
    else:
        log.info("Authentication disabled (demo mode). Set DASHBOARD_PASSWORD to enable it.")
