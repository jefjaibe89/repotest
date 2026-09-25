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

from flask import redirect, request, session, url_for

import config

log = logging.getLogger("sdwan-dashboard.auth")


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
