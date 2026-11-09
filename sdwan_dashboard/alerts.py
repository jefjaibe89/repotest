"""
Alert rules and webhook delivery.

Rules are evaluated on every poll. The hard part is not detecting problems but
not shouting about them: a device that has been down for an hour must not
produce 120 notifications. Each alert therefore carries a stable key, and a
key that is already active is only re-sent once its cooldown expires.
"""

import logging
import time

import requests

import config
import store

log = logging.getLogger("sdwan-dashboard.alerts")

SEVERITY_EMOJI = {"Critical": "🔴", "Major": "🟠", "Minor": "🟡", "Resolved": "🟢"}


# --------------------------------------------------------------------- rules
def evaluate(health: dict, summary: dict, devices: list[dict], bfd: list[dict]) -> dict[str, dict]:
    """Return the currently firing alerts, keyed by a stable identifier."""
    firing: dict[str, dict] = {}

    if health.get("score", 100) < config.ALERT_SCORE_THRESHOLD:
        firing["fabric:score"] = {
            "severity": "Critical" if health["score"] < 50 else "Major",
            "message": (
                f"Fabric health score is {health['score']} "
                f"({health.get('grade')}), below the {config.ALERT_SCORE_THRESHOLD} threshold"
            ),
        }

    for d in devices:
        if d.get("reachability") != "reachable":
            ip = d.get("system_ip") or d.get("hostname")
            firing[f"device:unreachable:{ip}"] = {
                "severity": "Critical",
                "message": f"{d.get('hostname', ip)} ({ip}) is unreachable",
            }

    for b in bfd:
        down = b.get("bfd-sessions-down", 0)
        if down > 0:
            host = b.get("host-name", b.get("system-ip"))
            firing[f"bfd:down:{b.get('system-ip')}"] = {
                "severity": "Major",
                "message": f"{host} has {down} BFD session(s) down",
            }

    if summary.get("alarms_critical", 0) >= config.ALERT_CRITICAL_ALARM_COUNT:
        firing["alarms:critical"] = {
            "severity": "Critical",
            "message": f"{summary['alarms_critical']} unacknowledged critical alarm(s)",
        }

    return firing


# ----------------------------------------------------------------- dispatching
def _format_slack(alert: dict, resolved: bool) -> dict:
    icon = SEVERITY_EMOJI["Resolved" if resolved else alert["severity"]]
    prefix = "RESOLVED" if resolved else alert["severity"].upper()
    return {"text": f"{icon} *[{prefix}]* {alert['message']}"}


def _format_teams(alert: dict, resolved: bool) -> dict:
    colour = "2EB886" if resolved else {"Critical": "D00000", "Major": "FF9A3C"}.get(
        alert["severity"], "FFD600"
    )
    prefix = "Resolved" if resolved else alert["severity"]
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": colour,
        "summary": "SD-WAN alert",
        "sections": [{
            "activityTitle": f"SD-WAN — {prefix}",
            "text": alert["message"],
        }],
    }


def _format_generic(alert: dict, resolved: bool) -> dict:
    return {
        "source": "cisco-sdwan-health-dashboard",
        "status": "resolved" if resolved else "firing",
        "severity": alert["severity"],
        "message": alert["message"],
        "timestamp": time.time(),
    }


FORMATTERS = {"slack": _format_slack, "teams": _format_teams, "generic": _format_generic}


def send(alert: dict, resolved: bool = False) -> bool:
    if not config.ALERT_WEBHOOK_URL:
        return False

    formatter = FORMATTERS.get(config.ALERT_WEBHOOK_FORMAT, _format_generic)
    try:
        resp = requests.post(
            config.ALERT_WEBHOOK_URL,
            json=formatter(alert, resolved),
            timeout=config.ALERT_WEBHOOK_TIMEOUT,
        )
        if resp.status_code >= 400:
            log.warning("Alert webhook returned HTTP %s", resp.status_code)
            return False
        return True
    except requests.RequestException as exc:
        # A dead webhook must never take the poller down with it.
        log.warning("Alert webhook delivery failed: %s", exc)
        return False


# ------------------------------------------------------------------ processing
def process(health: dict, summary: dict, devices: list[dict], bfd: list[dict]) -> dict:
    """Evaluate rules, notify about what changed, and persist the new state."""
    if not config.ALERTS_ENABLED:
        return {"firing": 0, "notified": 0, "resolved": 0}

    now = time.time()
    firing = evaluate(health, summary, devices, bfd)
    known = store.get_alert_state()

    notified = 0
    for key, alert in firing.items():
        previous = known.get(key)
        if previous is None:
            first_seen = now
        else:
            first_seen = previous["first_seen"]
            age = now - (previous["last_notified"] or 0)
            if age < config.ALERT_COOLDOWN_SECONDS:
                continue  # Already announced recently; stay quiet.

        if send(alert):
            notified += 1
        store.upsert_alert(key, alert["severity"], alert["message"], first_seen, now)

    # Anything previously firing that is no longer firing has recovered.
    recovered = [key for key in known if key not in firing]
    for key in recovered:
        send({"severity": "Resolved", "message": f"Recovered: {known[key]['message']}"},
             resolved=True)
    store.clear_alerts(recovered)

    return {"firing": len(firing), "notified": notified, "resolved": len(recovered)}
