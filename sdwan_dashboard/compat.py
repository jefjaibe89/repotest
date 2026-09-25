"""
What the dashboard needs from vManage, and how well each of those is known.

vManage's monitoring API is versioned by release and varies with licensing and
tenancy, and Cisco does not publish a machine-readable contract for most of it.
Rather than assert a compatibility matrix that cannot be checked, each source
is declared here with the evidence behind it, and the running dashboard reports
which ones the controller in front of it actually served.

`evidence` values:
  catalogued  the exact method and path appear in Cisco's own catalystwan SDK
              endpoint catalogue (ENDPOINTS.md, v0.41.6)
  variant     the SDK has this path but only in a different form, normally
              requiring a deviceId the dashboard does not send
  unverified  not present in that catalogue; written from the documented API
              and never validated against a live controller
"""

CATALOGUED = "catalogued"
VARIANT = "variant"
UNVERIFIED = "unverified"

# --------------------------------------------------------- controller range
# The oldest vManage train the dashboard targets. Below this the endpoints it
# uses predate the API shape it expects.
VMANAGE_MIN = (20, 3)

# The highest release the endpoint audit covers: Cisco's catalystwan SDK
# declares constraints up to 20.16, and none of them touch the endpoints used
# here. Anything above this is reported as newer than the audit, NOT as
# unsupported — refusing to run against a release that did not exist when this
# table was written would be a worse failure than running unverified.
VMANAGE_VERIFIED_TO = (20, 16)


def _parse(raw):
    if not raw:
        return None
    parts = []
    for piece in str(raw).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def check_controller(raw_version: str | None) -> dict:
    """Place this controller's release against the range the audit covers."""
    version = _parse(raw_version)
    fmt = lambda v: ".".join(str(n) for n in v)  # noqa: E731

    if version is None:
        status = "unknown"
    elif version[:2] < VMANAGE_MIN:
        status = "below_minimum"
    elif version[:2] > VMANAGE_VERIFIED_TO:
        status = "newer_than_verified"
    else:
        status = "within_audit"

    return {
        "version": raw_version,
        "status": status,
        "minimum": fmt(VMANAGE_MIN),
        "verified_to": fmt(VMANAGE_VERIFIED_TO),
    }

# Ordered as an operator would read them: the foundation first.
SOURCES = [
    {
        "key": "devices", "method": "GET", "path": "/device",
        "feeds": "nav.overview", "required": True, "evidence": CATALOGUED,
    },
    {
        "key": "server", "method": "GET", "path": "/client/server",
        "feeds": "compat.title", "required": False, "evidence": CATALOGUED,
    },
    {
        "key": "probe_classes", "method": "GET", "path": "/template/policy/list/appprobe",
        "feeds": "eaar.title", "required": False, "evidence": CATALOGUED,
    },
    {
        "key": "sla_definitions", "method": "GET", "path": "/template/policy/list/sla",
        "feeds": "eaar.title", "required": False, "evidence": CATALOGUED,
    },
    {
        "key": "counters", "method": "GET", "path": "/device/counters",
        "feeds": "nav.overview", "required": False, "evidence": UNVERIFIED,
    },
    {
        "key": "control", "method": "GET", "path": "/device/control/count",
        "feeds": "control.title", "required": False, "evidence": UNVERIFIED,
    },
    {
        "key": "bfd", "method": "GET", "path": "/device/bfd/summary",
        "feeds": "kpi.bfd_sessions", "required": False, "evidence": VARIANT,
        "note": "compat.note.per_device",
    },
    {
        "key": "omp", "method": "GET", "path": "/device/omp/summary",
        "feeds": "kpi.omp_peers", "required": False, "evidence": VARIANT,
        "note": "compat.note.per_device",
    },
    {
        "key": "interfaces", "method": "GET", "path": "/statistics/interface/aggregation",
        "feeds": "chart.throughput", "required": False, "evidence": UNVERIFIED,
    },
    {
        "key": "alarms", "method": "POST", "path": "/alarms",
        "feeds": "alarms.title", "required": False, "evidence": UNVERIFIED,
    },
    {
        "key": "tunnels", "method": "GET", "path": "/device/tunnel/statistics",
        "feeds": "tunnels.title", "required": False, "evidence": UNVERIFIED,
    },
    {
        "key": "qos", "method": "GET", "path": "/device/qos/scheduler",
        "feeds": "qos.title", "required": False, "evidence": UNVERIFIED,
    },
    {
        "key": "links", "method": "GET", "path": "/device/interface",
        "feeds": "links.title", "required": False, "evidence": VARIANT,
        "note": "compat.note.per_device",
    },
    {
        "key": "sla_classes", "method": "GET", "path": "/device/app-route/sla-class",
        "feeds": "aar.title", "required": False, "evidence": UNVERIFIED,
    },
    {
        "key": "app_route", "method": "GET", "path": "/device/app-route/statistics",
        "feeds": "aar.title", "required": False, "evidence": UNVERIFIED,
    },
    {
        "key": "app_route_events", "method": "POST", "path": "/event",
        "feeds": "aar.recent_switchovers", "required": False, "evidence": UNVERIFIED,
    },
]

BY_KEY = {s["key"]: s for s in SOURCES}


def summarise(degraded: dict[str, str] | None) -> dict:
    """Merge the static declarations with what the last poll actually managed."""
    degraded = degraded or {}
    rows = []
    for source in SOURCES:
        reason = degraded.get(source["key"])
        rows.append({
            **source,
            # "served" is the only column here that is a fact about this
            # controller rather than a claim about vManage in general.
            "served": reason is None,
            "reason": reason,
        })

    counts = {
        "total": len(rows),
        "served": sum(1 for r in rows if r["served"]),
        "catalogued": sum(1 for r in rows if r["evidence"] == CATALOGUED),
        "variant": sum(1 for r in rows if r["evidence"] == VARIANT),
        "unverified": sum(1 for r in rows if r["evidence"] == UNVERIFIED),
    }
    return {"sources": rows, "counts": counts}
