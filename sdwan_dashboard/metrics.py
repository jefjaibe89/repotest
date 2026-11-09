"""
Prometheus exposition.

A NOC already runs Prometheus and Grafana. Exposing what the poller has
already computed lets the fabric appear in the dashboards and alerting a team
uses every day, instead of being one more screen competing for attention.

Everything here comes from the same stored payload the web views read, so a
scrape costs the controller nothing.
"""

import config

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Label values carry hostnames, interface names and TLOC colours — all of it
# from vManage rather than from us. The exposition format is line-based and
# quote-delimited, so the same characters that mattered for HTML matter here.
_LABEL_ESCAPES = str.maketrans({
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
})


def _label(value) -> str:
    if value is None:
        return ""
    return str(value).translate(_LABEL_ESCAPES)


def _line(name: str, value, labels: dict | None = None) -> str | None:
    """One sample. Returns None when there is no value to report.

    A missing reading is left out rather than written as zero: an unreachable
    device reports no CPU, and 0% would look like an idle one.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if labels:
        rendered = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items() if v is not None)
        return f"{name}{{{rendered}}} {number:g}"
    return f"{name} {number:g}"


class _Writer:
    def __init__(self):
        self.lines: list[str] = []

    def family(self, name: str, kind: str, help_text: str):
        self.lines.append(f"# HELP {name} {help_text}")
        self.lines.append(f"# TYPE {name} {kind}")

    def sample(self, name: str, value, labels: dict | None = None):
        line = _line(name, value, labels)
        if line is not None:
            self.lines.append(line)

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"


def render(payload: dict, meta: dict) -> str:
    """Turn one stored poll into the Prometheus text format."""
    w = _Writer()

    _collection(w, payload, meta)
    _fabric(w, payload)
    _devices(w, payload)
    _alarms(w, payload)
    _qos(w, payload)
    _links(w, payload)
    _aar(w, payload)
    _enhanced_aar(w, payload)

    return w.render()


# ----------------------------------------------------- collection health
def _collection(w, payload, meta):
    """Whether the dashboard is still collecting — distinct from fabric health.

    Without this a scraper cannot tell a healthy fabric from a poller that
    stopped an hour ago and is serving the last good figures.
    """
    w.family("sdwan_up", "gauge", "1 when the last poll succeeded, 0 otherwise")
    w.sample("sdwan_up", 0 if meta.get("error") else 1)

    w.family("sdwan_poll_age_seconds", "gauge",
             "Seconds since the fabric was last collected")
    w.sample("sdwan_poll_age_seconds", meta.get("age_seconds"))

    w.family("sdwan_poll_consecutive_failures", "gauge",
             "Consecutive failed polls; 0 after any success")
    w.sample("sdwan_poll_consecutive_failures", meta.get("consecutive_failures", 0))

    compat = payload.get("compat", {})
    w.family("sdwan_sources_degraded", "gauge",
             "Data sources this controller did not serve on the last poll")
    w.sample("sdwan_sources_degraded", compat.get("sources_degraded", 0))


# ------------------------------------------------------------- fabric
def _fabric(w, payload):
    health = payload.get("health", {})

    w.family("sdwan_fabric_health_score", "gauge",
             "Overall fabric health, 0-100")
    w.sample("sdwan_fabric_health_score", health.get("score"))

    w.family("sdwan_fabric_health_category_score", "gauge",
             "Health score of one scoring category, 0-100")
    for name, entry in (health.get("categories") or {}).items():
        w.sample("sdwan_fabric_health_category_score", entry.get("score"),
                 {"category": name})

    w.family("sdwan_fabric_health_category_weight", "gauge",
             "Weight a category carries in the overall score")
    for name, entry in (health.get("categories") or {}).items():
        w.sample("sdwan_fabric_health_category_weight", entry.get("weight"),
                 {"category": name})


# ------------------------------------------------------------ devices
def _devices(w, payload):
    summary = payload.get("summary", {})

    w.family("sdwan_devices_total", "gauge", "Devices in the fabric")
    w.sample("sdwan_devices_total", summary.get("total_devices"))

    w.family("sdwan_devices_reachable", "gauge", "Devices vManage can reach")
    w.sample("sdwan_devices_reachable", summary.get("reachable"))

    w.family("sdwan_devices_unreachable", "gauge", "Devices vManage cannot reach")
    w.sample("sdwan_devices_unreachable", summary.get("unreachable"))

    w.family("sdwan_bfd_sessions", "gauge", "BFD sessions by state")
    w.sample("sdwan_bfd_sessions", summary.get("bfd_up"), {"state": "up"})
    w.sample("sdwan_bfd_sessions", summary.get("bfd_down"), {"state": "down"})

    w.family("sdwan_omp_peers_healthy", "gauge", "Devices with healthy OMP peering")
    w.sample("sdwan_omp_peers_healthy", summary.get("omp_up"))

    devices = payload.get("devices") or []
    labels = lambda d: {  # noqa: E731
        "hostname": d.get("hostname"),
        "system_ip": d.get("system_ip"),
        "site_id": d.get("site_id"),
        "device_type": d.get("device_type"),
    }

    w.family("sdwan_device_reachable", "gauge", "1 when a device is reachable")
    for d in devices:
        w.sample("sdwan_device_reachable",
                 1 if d.get("reachability") == "reachable" else 0, labels(d))

    w.family("sdwan_device_cpu_percent", "gauge", "Device CPU load")
    for d in devices:
        w.sample("sdwan_device_cpu_percent", d.get("cpu"), labels(d))

    w.family("sdwan_device_memory_percent", "gauge", "Device memory use")
    for d in devices:
        w.sample("sdwan_device_memory_percent", d.get("memory"), labels(d))


# ------------------------------------------------------------- alarms
def _alarms(w, payload):
    summary = payload.get("summary", {})
    w.family("sdwan_alarms_active", "gauge",
             "Unacknowledged alarms by severity")
    for severity, key in (("Critical", "alarms_critical"),
                          ("Major", "alarms_major"),
                          ("Minor", "alarms_minor")):
        w.sample("sdwan_alarms_active", summary.get(key), {"severity": severity})


# ---------------------------------------------------------------- QoS
def _qos(w, payload):
    qos = payload.get("qos", {})
    totals = qos.get("totals", {})

    w.family("sdwan_qos_dropped_packets_total", "counter",
             "Packets dropped by the QoS policy")
    w.family("sdwan_qos_sent_packets_total", "counter",
             "Packets sent through the QoS policy")

    for q in qos.get("queues") or []:
        labels = {
            "hostname": q.get("hostname"),
            "interface": q.get("interface"),
            "queue": q.get("queue"),
            "class": q.get("klass"),
        }
        w.sample("sdwan_qos_dropped_packets_total", q.get("drops"), labels)
        w.sample("sdwan_qos_sent_packets_total", q.get("tx_packets"), labels)

    w.family("sdwan_qos_realtime_dropped_packets_total", "counter",
             "Packets dropped from real-time queues, where any drop is a fault")
    w.sample("sdwan_qos_realtime_dropped_packets_total", totals.get("realtime_drops"))


# -------------------------------------------------------------- links
def _links(w, payload):
    links = payload.get("links", {})

    w.family("sdwan_link_up", "gauge", "1 when a circuit is operationally up")
    w.family("sdwan_link_bandwidth_kbps", "gauge",
             "Bandwidth the circuit is configured with, by direction")
    w.family("sdwan_link_throughput_kbps", "gauge",
             "Current throughput, by direction")
    w.family("sdwan_link_utilisation_percent", "gauge",
             "Throughput as a share of configured bandwidth")

    for link in links.get("links") or []:
        base = {
            "hostname": link.get("hostname"),
            "interface": link.get("interface"),
            "color": link.get("color"),
        }
        w.sample("sdwan_link_up", 1 if link.get("oper_up") else 0, base)
        w.sample("sdwan_link_bandwidth_kbps", link.get("bw_up"), {**base, "direction": "tx"})
        w.sample("sdwan_link_bandwidth_kbps", link.get("bw_down"), {**base, "direction": "rx"})
        w.sample("sdwan_link_throughput_kbps", link.get("tx_kbps"), {**base, "direction": "tx"})
        w.sample("sdwan_link_throughput_kbps", link.get("rx_kbps"), {**base, "direction": "rx"})
        w.sample("sdwan_link_utilisation_percent", link.get("tx_pct"), {**base, "direction": "tx"})
        w.sample("sdwan_link_utilisation_percent", link.get("rx_pct"), {**base, "direction": "rx"})

    totals = links.get("totals", {})
    w.family("sdwan_links_saturated", "gauge",
             "Circuits at or above the saturation threshold")
    w.sample("sdwan_links_saturated", totals.get("saturated"))


# ---------------------------------------------------------------- AAR
def _aar(w, payload):
    aar = payload.get("aar", {})
    totals = aar.get("totals", {})

    w.family("sdwan_aar_compliance_percent", "gauge",
             "Tunnels within their SLA budget, as a percentage")
    w.sample("sdwan_aar_compliance_percent", totals.get("compliance_pct"))

    w.family("sdwan_aar_tunnels_out_of_sla", "gauge",
             "Tunnels currently breaching their SLA budget")
    w.sample("sdwan_aar_tunnels_out_of_sla", totals.get("violating"))

    w.family("sdwan_aar_class_compliance_percent", "gauge",
             "Compliance within one SLA class")
    for klass in aar.get("classes") or []:
        w.sample("sdwan_aar_class_compliance_percent", klass.get("compliance_pct"),
                 {"sla_class": klass.get("name")})

    w.family("sdwan_aar_tunnel_latency_ms", "gauge", "Tunnel latency")
    w.family("sdwan_aar_tunnel_loss_percent", "gauge", "Tunnel packet loss")
    w.family("sdwan_aar_tunnel_jitter_ms", "gauge", "Tunnel jitter")
    w.family("sdwan_aar_tunnel_in_sla", "gauge",
             "1 when a tunnel is inside the SLA class bound to it")

    for tunnel in aar.get("tunnels") or []:
        labels = {
            "hostname": tunnel.get("hostname"),
            "local_color": tunnel.get("local_color"),
            "remote": tunnel.get("remote_ip"),
            "sla_class": tunnel.get("sla_class"),
        }
        w.sample("sdwan_aar_tunnel_latency_ms", tunnel.get("latency"), labels)
        w.sample("sdwan_aar_tunnel_loss_percent", tunnel.get("loss"), labels)
        w.sample("sdwan_aar_tunnel_jitter_ms", tunnel.get("jitter"), labels)
        w.sample("sdwan_aar_tunnel_in_sla", 1 if tunnel.get("in_sla") else 0, labels)

    w.family("sdwan_aar_switchovers", "gauge",
             "Path switchovers in the alarm window, by reason")
    for reason, count in (totals.get("by_reason") or {}).items():
        w.sample("sdwan_aar_switchovers", count, {"reason": reason})


# ------------------------------------------------------- enhanced AAR
def _enhanced_aar(w, payload):
    eaar = payload.get("enhanced_aar", {})
    totals = eaar.get("totals", {})

    w.family("sdwan_enhanced_aar_sla_classes", "gauge",
             "SLA classes by how they are probed")
    w.sample("sdwan_enhanced_aar_sla_classes", totals.get("sla_enhanced"),
             {"probing": "enhanced"})
    w.sample("sdwan_enhanced_aar_sla_classes", totals.get("sla_default"),
             {"probing": "default"})

    w.family("sdwan_enhanced_aar_edges_capable", "gauge",
             "Edges on a release that can run enhanced AAR")
    w.sample("sdwan_enhanced_aar_edges_capable", totals.get("devices_supported"))

    w.family("sdwan_enhanced_aar_edges_blocking", "gauge",
             "Edges on a release too old to run enhanced AAR")
    w.sample("sdwan_enhanced_aar_edges_blocking", totals.get("devices_blocking"))


# ------------------------------------------------------------- access
def authorised(auth_header: str | None) -> bool:
    """Check the scrape token, when one is configured.

    A scraper cannot log in through a session, so /metrics cannot sit behind
    the dashboard's own login. A bearer token is the way to protect it, and
    config warns when the endpoint is exposed without one.
    """
    token = config.METRICS_TOKEN
    if not token:
        return True

    if not auth_header:
        return False
    scheme, _, presented = auth_header.partition(" ")
    if scheme.lower() != "bearer":
        return False

    # Constant-time, so the token cannot be recovered a character at a time.
    import hmac
    return hmac.compare_digest(presented.strip(), token)
