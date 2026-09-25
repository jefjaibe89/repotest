"""
Cisco Catalyst SD-WAN Health Check Dashboard — Flask backend
"""

import csv
import io
import logging
import threading
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, Response, jsonify, render_template

import config
import health
from sdwan_client import (
    MockSDWANClient,
    SDWANAuthError,
    SDWANClient,
    SDWANConnectionError,
    SDWANError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("sdwan-dashboard")

app = Flask(__name__)

# A logged-in vManage session is reused across requests. Re-authenticating on
# every call costs an extra round trip per widget and vManage throttles logins.
_client_lock = threading.Lock()
_cached_client: SDWANClient | None = None
_cached_at: float = 0.0


def _get_client():
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


def _invalidate_client():
    global _cached_client, _cached_at
    with _client_lock:
        _cached_client, _cached_at = None, 0.0


def api_route(rule: str):
    """Register a JSON endpoint that turns SD-WAN failures into clean 502s."""

    def decorator(fn):
        @app.route(rule)
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except SDWANAuthError as exc:
                # The cached session is no good any more; the next call re-logs in.
                _invalidate_client()
                log.warning("vManage auth failure on %s: %s", rule, exc)
                return jsonify({"error": "auth", "message": str(exc)}), 502
            except SDWANConnectionError as exc:
                log.warning("vManage unreachable on %s: %s", rule, exc)
                return jsonify({"error": "connection", "message": str(exc)}), 502
            except SDWANError as exc:
                log.exception("SD-WAN error on %s", rule)
                return jsonify({"error": "sdwan", "message": str(exc)}), 502

        return wrapper

    return decorator


def _normalize_device(d: dict) -> dict:
    return {
        "system_ip": d.get("system-ip"),
        "hostname": d.get("host-name"),
        "device_type": d.get("device-type"),
        "model": d.get("device-model", "—"),
        "version": d.get("version", "—"),
        "site_id": d.get("site-id", "—"),
        "reachability": d.get("reachability", "unknown"),
        "status": d.get("status", "unknown"),
        "cpu": d.get("cpu-load"),
        "memory": d.get("mem-util"),
        "serial": d.get("board-serial", "—"),
        "uptime_ms": d.get("uptime-date"),
    }


# ------------------------------------------------------------------ UI route
@app.route("/")
def index():
    return render_template(
        "index.html",
        refresh_interval=config.REFRESH_INTERVAL_SECONDS,
        mode=config.MODE,
        vmanage_host=config.VMANAGE_HOST,
    )


# ------------------------------------------------- liveness (for containers)
@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "mode": config.MODE})


# ----------------------------------------------------------- API: summary
@api_route("/api/summary")
def api_summary():
    client = _get_client()
    devices = client.get_device_list()
    counters = client.get_device_counters()
    alarms = client.get_alarms(config.ALARM_WINDOW_HOURS)
    bfd = client.get_bfd_sessions()
    omp = client.get_omp_peers()

    total_bfd_up = sum(d.get("bfd-sessions-up", 0) for d in bfd)
    total_bfd_down = sum(d.get("bfd-sessions-down", 0) for d in bfd)
    omp_up = sum(1 for d in omp if d.get("oper-state") == "up")

    open_alarms = [a for a in alarms if not a.get("acknowledged")]
    by_severity = {sev: 0 for sev in ("Critical", "Major", "Minor")}
    for a in open_alarms:
        sev = a.get("severity")
        if sev in by_severity:
            by_severity[sev] += 1

    unreachable = counters.get(
        "unreachableCount",
        sum(1 for d in devices if d.get("reachability") != "reachable"),
    )

    return jsonify({
        "total_devices": counters.get("totalCount", len(devices)),
        "reachable": counters.get(
            "reachableCount",
            sum(1 for d in devices if d.get("reachability") == "reachable"),
        ),
        "unreachable": unreachable,
        "bfd_up": total_bfd_up,
        "bfd_down": total_bfd_down,
        "omp_up": omp_up,
        "omp_total": len(omp),
        "alarms_critical": by_severity["Critical"],
        "alarms_major": by_severity["Major"],
        "alarms_minor": by_severity["Minor"],
    })


# ------------------------------------------------------- API: health score
@api_route("/api/health")
def api_health():
    client = _get_client()
    report = health.compute(
        devices=client.get_device_list(),
        bfd=client.get_bfd_sessions(),
        control=client.get_control_status(),
        alarms=client.get_alarms(config.ALARM_WINDOW_HOURS),
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    return jsonify(report)


# ----------------------------------------------------------- API: devices
@api_route("/api/devices")
def api_devices():
    client = _get_client()
    return jsonify([_normalize_device(d) for d in client.get_device_list()])


# ----------------------------------------------------------- API: alarms
@api_route("/api/alarms")
def api_alarms():
    client = _get_client()
    return jsonify(client.get_alarms(config.ALARM_WINDOW_HOURS))


# ----------------------------------------------------------- API: bfd
@api_route("/api/bfd")
def api_bfd():
    return jsonify(_get_client().get_bfd_sessions())


# ----------------------------------------------------------- API: omp
@api_route("/api/omp")
def api_omp():
    return jsonify(_get_client().get_omp_peers())


# ----------------------------------------------- API: interface stats
@api_route("/api/interfaces")
def api_interfaces():
    return jsonify(_get_client().get_interface_stats())


# ----------------------------------------------- API: control plane
@api_route("/api/control")
def api_control():
    return jsonify(_get_client().get_control_status())


# --------------------------------------------------- Export: devices as CSV
@app.route("/api/export/devices.csv")
def export_devices_csv():
    try:
        devices = [_normalize_device(d) for d in _get_client().get_device_list()]
    except SDWANError as exc:
        _invalidate_client()
        return jsonify({"error": "sdwan", "message": str(exc)}), 502

    columns = [
        "hostname", "system_ip", "device_type", "model", "version",
        "site_id", "reachability", "status", "cpu", "memory", "serial",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(devices)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="sdwan-devices-{stamp}.csv"'
        },
    )


if __name__ == "__main__":
    log.info("Starting dashboard in %s mode on %s:%s",
             config.MODE, config.FLASK_HOST, config.FLASK_PORT)
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
    )
