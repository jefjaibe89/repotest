"""
Cisco Catalyst SD-WAN Health Check Dashboard — Flask backend

Web requests never touch vManage: a background poller collects the fabric on a
fixed cadence and writes it to the store, and every route serves from there.
"""

import csv
import io
import logging
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import auth
import config
import poller
import store
from sdwan_client import MockSDWANClient, SDWANClient, SDWANError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("sdwan-dashboard")

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
)

# A logged-in vManage session is reused across polls. Re-authenticating on every
# poll costs a round trip and vManage throttles logins.
_client_lock = threading.Lock()
_cached_client: SDWANClient | None = None
_cached_at: float = 0.0


def get_client():
    """Return a ready-to-use client, reusing the vManage session when possible."""
    global _cached_client, _cached_at

    if config.MODE != "live":
        return MockSDWANClient()

    with _client_lock:
        fresh = (time.time() - _cached_at) < config.SESSION_TTL_SECONDS
        if _cached_client is not None and _cached_client.authenticated and fresh:
            return _cached_client

        log.info("Authenticating against vManage at %s", config.VMANAGE_HOST)
        client = SDWANClient(
            host=config.VMANAGE_HOST,
            port=config.VMANAGE_PORT,
            username=config.VMANAGE_USER,
            password=config.VMANAGE_PASS,
            verify_ssl=config.VMANAGE_VERIFY_SSL,
            timeout=config.VMANAGE_TIMEOUT,
        )
        client.login()
        _cached_client, _cached_at = client, time.time()
        return client


def invalidate_client():
    global _cached_client, _cached_at
    with _client_lock:
        _cached_client, _cached_at = None, 0.0


# --------------------------------------------------------------- data access
def _latest() -> tuple[dict, dict]:
    """Return (payload, meta). Raises LookupError when nothing has been collected."""
    record = store.get_latest()
    if record is None or not record["payload"]:
        raise LookupError(record["error"] if record else "No data collected yet")

    age = time.time() - record["fetched_at"]
    meta = {
        "fetched_at": record["fetched_at"],
        "age_seconds": round(age, 1),
        "error": record["error"],
        "consecutive_failures": record["consecutive_failures"],
        # Data is stale once it is older than two polling cycles.
        "stale": bool(record["error"]) or age > config.POLL_INTERVAL_SECONDS * 2,
    }
    return record["payload"], meta


def served(section: str):
    """Serve one section of the latest payload, or 503 when there is nothing yet."""
    try:
        payload, meta = _latest()
    except LookupError as exc:
        return jsonify({"error": "no_data", "message": str(exc)}), 503

    resp = jsonify(payload.get(section))
    resp.headers["X-Data-Age"] = str(meta["age_seconds"])
    resp.headers["X-Data-Stale"] = "1" if meta["stale"] else "0"
    return resp


def _safe_next(target: str | None) -> str | None:
    """Return `target` only if it is a same-site path, else None.

    A leading-slash check is not enough. "//evil.example.com" and
    "/\\evil.example.com" both start with "/" but browsers resolve them as
    absolute cross-origin URLs, and Werkzeug passes the Location header
    through untouched — so the login would redirect off-site after the
    operator has just typed valid credentials.
    """
    if not target or not target.startswith("/"):
        return None
    if target.startswith("//") or target.startswith("/\\"):
        return None
    parts = urlparse(target)
    if parts.scheme or parts.netloc:
        return None
    return target


# ------------------------------------------------------------------ UI routes
@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth.enabled():
        return redirect(url_for("index"))

    cid = auth.client_id()
    locked = auth.lockout_remaining(cid)

    if request.method == "POST":
        if locked:
            # Refuse without checking the password, so a locked-out client
            # learns nothing from how the response differs.
            flash(f"Too many failed attempts. Try again in {locked // 60 + 1} min.")
            return render_template("login.html", locked=locked), 429

        if auth.check_credentials(request.form.get("username"), request.form.get("password")):
            auth.register_success(cid)
            session.clear()
            session["authenticated"] = True
            session.permanent = False
            return redirect(_safe_next(request.args.get("next")) or url_for("index"))

        lockout = auth.register_failure(cid)
        if lockout:
            flash(f"Too many failed attempts. Locked for {lockout // 60} min.")
            return render_template("login.html", locked=lockout), 429
        flash("Invalid credentials")

    return render_template("login.html", locked=locked)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login") if auth.enabled() else url_for("index"))


@app.route("/")
@auth.login_required
def index():
    return render_template(
        "index.html",
        refresh_interval=config.REFRESH_INTERVAL_SECONDS,
        mode=config.MODE,
        vmanage_host=config.VMANAGE_HOST,
        auth_enabled=auth.enabled(),
        alerts_enabled=config.ALERTS_ENABLED,
    )


# ------------------------------------------------- liveness (for containers)
@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "mode": config.MODE})


# ------------------------------------------------------------ poll metadata
@app.route("/api/status")
@auth.login_required
def api_status():
    record = store.get_latest()
    if record is None:
        return jsonify({"has_data": False, "stale": True, "error": "No data collected yet"})
    age = time.time() - record["fetched_at"]
    return jsonify({
        "has_data": bool(record["payload"]),
        "fetched_at": record["fetched_at"],
        "age_seconds": round(age, 1),
        "error": record["error"],
        "consecutive_failures": record["consecutive_failures"],
        "stale": bool(record["error"]) or age > config.POLL_INTERVAL_SECONDS * 2,
        "poll_interval": config.POLL_INTERVAL_SECONDS,
    })


# ----------------------------------------------------------- panel endpoints
@app.route("/api/summary")
@auth.login_required
def api_summary():
    return served("summary")


@app.route("/api/health")
@auth.login_required
def api_health():
    return served("health")


@app.route("/api/devices")
@auth.login_required
def api_devices():
    return served("devices")


@app.route("/api/alarms")
@auth.login_required
def api_alarms():
    return served("alarms")


@app.route("/api/bfd")
@auth.login_required
def api_bfd():
    return served("bfd")


@app.route("/api/omp")
@auth.login_required
def api_omp():
    return served("omp")


@app.route("/api/interfaces")
@auth.login_required
def api_interfaces():
    return served("interfaces")


@app.route("/api/control")
@auth.login_required
def api_control():
    return served("control")


@app.route("/api/tunnels")
@auth.login_required
def api_tunnels():
    return served("tunnels")


# ------------------------------------------------------------------- history
@app.route("/api/history")
@auth.login_required
def api_history():
    hours = request.args.get("hours", config.HISTORY_WINDOW_HOURS, type=int)
    return jsonify(store.get_history(hours=max(1, min(hours, 720))))


# -------------------------------------------------------------- drill-down
@app.route("/api/device/<system_ip>")
@auth.login_required
def api_device_detail(system_ip):
    try:
        payload, _ = _latest()
    except LookupError as exc:
        return jsonify({"error": "no_data", "message": str(exc)}), 503

    device = next(
        (d for d in payload.get("devices", []) if d.get("system_ip") == system_ip), None
    )
    if device is None:
        return jsonify({"error": "not_found", "message": f"No device with system IP {system_ip}"}), 404

    # The per-device views are not part of the polled payload: they are only
    # needed when someone actually opens a device, so they are fetched on demand.
    try:
        client = get_client()
        detail = {
            "device": device,
            "interfaces": client.get_device_interfaces(system_ip),
            "tunnels": client.get_device_tunnels(system_ip),
            "control_connections": client.get_device_control_connections(system_ip),
            "omp_routes": client.get_device_omp_routes(system_ip),
        }
    except SDWANError as exc:
        invalidate_client()
        return jsonify({"error": "sdwan", "message": str(exc)}), 502

    detail["alarms"] = [
        a for a in payload.get("alarms", [])
        if device.get("hostname") and device["hostname"] in str(a.get("message", ""))
    ]
    detail["history"] = store.get_device_history(system_ip, hours=config.HISTORY_WINDOW_HOURS)
    return jsonify(detail)


# --------------------------------------------------- Export: devices as CSV
@app.route("/api/export/devices.csv")
@auth.login_required
def export_devices_csv():
    try:
        payload, _ = _latest()
    except LookupError as exc:
        return jsonify({"error": "no_data", "message": str(exc)}), 503

    columns = [
        "hostname", "system_ip", "device_type", "model", "version",
        "site_id", "reachability", "status", "cpu", "memory", "serial",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(payload.get("devices", []))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="sdwan-devices-{stamp}.csv"'},
    )


# ------------------------------------------------------------------ bootstrap
def bootstrap():
    """Prepare the store and start polling. Safe to call from every worker."""
    store.init()
    auth.warn_if_unprotected()

    if config.MODE == "live" and config.VMANAGE_VERIFY_SSL is False:
        log.warning(
            "VMANAGE_VERIFY_SSL is off: vManage credentials are sent over a TLS "
            "connection whose certificate is not checked. Point it at the CA "
            "bundle that issued the controller's certificate instead."
        )

    if auth.enabled() and not os.getenv("SECRET_KEY"):
        # Each worker would generate its own key, so a session signed by one is
        # rejected by the next and operators get logged out at random.
        log.warning(
            "SECRET_KEY is not set: a random key is generated per process, so "
            "logins will not survive a restart and will break across workers. "
            "Set SECRET_KEY to a fixed value."
        )
    if config.ALERTS_ENABLED:
        log.info("Alerting enabled (%s webhook)", config.ALERT_WEBHOOK_FORMAT)
    poller.start(get_client)


bootstrap()


if __name__ == "__main__":
    log.info("Starting dashboard in %s mode on %s:%s",
             config.MODE, config.FLASK_HOST, config.FLASK_PORT)
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
        # The reloader would fork a second poller.
        use_reloader=False,
    )
