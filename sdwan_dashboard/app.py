"""
Cisco Catalyst SD-WAN Health Check Dashboard — Flask backend
"""

from flask import Flask, jsonify, render_template
import config
from sdwan_client import MockSDWANClient, SDWANClient

app = Flask(__name__)


def _get_client():
    if config.MODE == "live":
        client = SDWANClient(
            host=config.VMANAGE_HOST,
            port=config.VMANAGE_PORT,
            username=config.VMANAGE_USER,
            password=config.VMANAGE_PASS,
            verify_ssl=config.VMANAGE_VERIFY_SSL,
        )
        client.login()
        return client
    return MockSDWANClient()


# ------------------------------------------------------------------ UI route
@app.route("/")
def index():
    return render_template(
        "index.html",
        refresh_interval=config.REFRESH_INTERVAL_SECONDS,
        mode=config.MODE,
        vmanage_host=config.VMANAGE_HOST,
    )


# ----------------------------------------------------------- API: summary
@app.route("/api/summary")
def api_summary():
    client = _get_client()
    devices = client.get_device_list()
    counters = client.get_device_counters()
    alarms = client.get_alarms()
    bfd = client.get_bfd_sessions()
    omp = client.get_omp_peers()

    total_bfd_up = sum(d.get("bfd-sessions-up", 0) for d in bfd)
    total_bfd_down = sum(d.get("bfd-sessions-down", 0) for d in bfd)
    omp_up = sum(1 for d in omp if d.get("oper-state") == "up")

    critical = sum(1 for a in alarms if a.get("severity") == "Critical" and not a.get("acknowledged"))
    major = sum(1 for a in alarms if a.get("severity") == "Major" and not a.get("acknowledged"))
    minor = sum(1 for a in alarms if a.get("severity") == "Minor" and not a.get("acknowledged"))

    return jsonify({
        "total_devices": counters.get("totalCount", len(devices)),
        "reachable": counters.get("reachableCount", 0),
        "unreachable": counters.get("unreachableCount", 0),
        "bfd_up": total_bfd_up,
        "bfd_down": total_bfd_down,
        "omp_up": omp_up,
        "omp_total": len(omp),
        "alarms_critical": critical,
        "alarms_major": major,
        "alarms_minor": minor,
    })


# ----------------------------------------------------------- API: devices
@app.route("/api/devices")
def api_devices():
    client = _get_client()
    devices = client.get_device_list()
    result = []
    for d in devices:
        result.append({
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
        })
    return jsonify(result)


# ----------------------------------------------------------- API: alarms
@app.route("/api/alarms")
def api_alarms():
    client = _get_client()
    alarms = client.get_alarms()
    return jsonify(alarms)


# ----------------------------------------------------------- API: bfd
@app.route("/api/bfd")
def api_bfd():
    client = _get_client()
    return jsonify(client.get_bfd_sessions())


# ----------------------------------------------------------- API: omp
@app.route("/api/omp")
def api_omp():
    client = _get_client()
    return jsonify(client.get_omp_peers())


# ----------------------------------------------- API: interface stats
@app.route("/api/interfaces")
def api_interfaces():
    client = _get_client()
    return jsonify(client.get_interface_stats())


# ----------------------------------------------- API: control plane
@app.route("/api/control")
def api_control():
    client = _get_client()
    return jsonify(client.get_control_status())


if __name__ == "__main__":
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
    )
