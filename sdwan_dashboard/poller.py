"""
Background poller.

Every browser used to query vManage on its own, so ten NOC screens meant ten
times the load on the controller. Instead one poller fetches the fabric on a
fixed cadence and writes the result to the store; every web worker then serves
identical data from there, and the load on vManage stays constant no matter how
many people are watching.

Under gunicorn each worker imports this module, so an advisory file lock keeps
exactly one of them polling. Workers that lose the race simply read the store.
"""

import fcntl
import logging
import threading
import time

import alerts
import analysis
import config
import health as health_mod
import store
from sdwan_client import SDWANError

log = logging.getLogger("sdwan-dashboard.poller")

_lock_handle = None
_thread = None
_consecutive_failures = 0


def acquire_lock() -> bool:
    """Try to become the polling process. Non-blocking; False means someone else won."""
    global _lock_handle
    try:
        handle = open(config.POLLER_LOCK_PATH, "w")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        return False
    _lock_handle = handle  # Held for the process lifetime; closing releases the lock.
    return True


def collect(client) -> dict:
    """Fetch every panel's data and score it. Raises SDWANError on failure."""
    devices_raw = client.get_device_list()
    counters = client.get_device_counters()
    bfd = client.get_bfd_sessions()
    omp = client.get_omp_peers()
    control = client.get_control_status()
    interfaces = client.get_interface_stats()
    alarms_list = client.get_alarms(config.ALARM_WINDOW_HOURS)
    tunnels = client.get_tunnel_stats()

    # Inputs for the specialised views. Collected on the same cadence as
    # everything else, so opening one of them costs the controller nothing.
    qos = analysis.analyse_qos(client.get_qos_stats())
    links = analysis.analyse_links(client.get_link_stats())
    aar = analysis.analyse_aar(
        stats=client.get_app_route_stats(),
        classes=client.get_sla_classes(),
        events=client.get_app_route_events(config.ALARM_WINDOW_HOURS),
    )

    report = health_mod.compute(
        devices=devices_raw, bfd=bfd, control=control, alarms=alarms_list
    )

    open_alarms = [a for a in alarms_list if not a.get("acknowledged")]
    by_severity = {sev: 0 for sev in ("Critical", "Major", "Minor")}
    for a in open_alarms:
        if a.get("severity") in by_severity:
            by_severity[a["severity"]] += 1

    summary = {
        "total_devices": counters.get("totalCount", len(devices_raw)),
        "reachable": counters.get(
            "reachableCount",
            sum(1 for d in devices_raw if d.get("reachability") == "reachable"),
        ),
        "unreachable": counters.get(
            "unreachableCount",
            sum(1 for d in devices_raw if d.get("reachability") != "reachable"),
        ),
        "bfd_up": sum(d.get("bfd-sessions-up", 0) for d in bfd),
        "bfd_down": sum(d.get("bfd-sessions-down", 0) for d in bfd),
        "omp_up": sum(1 for d in omp if d.get("oper-state") == "up"),
        "omp_total": len(omp),
        "alarms_critical": by_severity["Critical"],
        "alarms_major": by_severity["Major"],
        "alarms_minor": by_severity["Minor"],
    }

    return {
        "summary": summary,
        "health": report,
        "devices": [normalize_device(d) for d in devices_raw],
        "devices_raw": devices_raw,
        "alarms": alarms_list,
        "bfd": bfd,
        "omp": omp,
        "control": control,
        "interfaces": interfaces,
        "tunnels": tunnels,
        "qos": qos,
        "links": links,
        "aar": aar,
    }


def normalize_device(d: dict) -> dict:
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


def poll_once(client_factory) -> bool:
    """Run one polling cycle. Returns True when it succeeded."""
    global _consecutive_failures

    try:
        payload = collect(client_factory())
    except SDWANError as exc:
        _consecutive_failures += 1
        log.warning("Poll failed (%s consecutive): %s", _consecutive_failures, exc)
        store.record_failure(str(exc), _consecutive_failures, getattr(exc, "key", None))
        return False
    except Exception as exc:  # noqa: BLE001 - the poller thread must never die
        _consecutive_failures += 1
        log.exception("Unexpected poll error")
        store.record_failure(f"Internal error: {exc}", _consecutive_failures)
        return False

    _consecutive_failures = 0
    now = time.time()
    store.set_latest(payload, now, None, 0)
    store.add_snapshot(now, payload["health"], payload["summary"])
    store.add_device_samples(now, payload["devices"])

    try:
        result = alerts.process(
            payload["health"], payload["summary"], payload["devices"], payload["bfd"]
        )
        if result["notified"] or result["resolved"]:
            log.info("Alerts: %s sent, %s resolved", result["notified"], result["resolved"])
    except Exception:  # noqa: BLE001 - alerting must not break data collection
        log.exception("Alert processing failed")

    return True


def _loop(client_factory):
    prune_every = max(1, int(3600 / max(config.POLL_INTERVAL_SECONDS, 1)))
    tick = 0
    while True:
        poll_once(client_factory)
        tick += 1
        if tick % prune_every == 0:
            try:
                store.prune()
            except Exception:  # noqa: BLE001
                log.exception("History pruning failed")
        time.sleep(config.POLL_INTERVAL_SECONDS)


def start(client_factory):
    """Start polling in this process if no other process is already doing it."""
    global _thread

    if _thread is not None:
        return False
    if not acquire_lock():
        log.info("Another process holds the poller lock; serving from the store")
        return False

    # Populate the store before the first request arrives, so the dashboard
    # never opens on an empty page.
    poll_once(client_factory)

    _thread = threading.Thread(target=_loop, args=(client_factory,), daemon=True)
    _thread.start()
    log.info("Poller started (every %ss)", config.POLL_INTERVAL_SECONDS)
    return True
