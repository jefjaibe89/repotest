"""
Turns raw counters into the judgements each specialised view needs.

vManage reports totals; an operator needs ratios and verdicts. Drop counts mean
nothing without the packet count beside them, a 184 Mbps circuit means nothing
without knowing it was sold as 200, and a 78 ms tunnel is fine or broken purely
depending on which SLA class is bound to it.

Computed once per poll so every worker serves the same numbers.
"""

import config

# A queue is called out once its drop ratio crosses these.
QOS_WARN_RATIO = 0.001   # 0.1 %
QOS_CRIT_RATIO = 0.01    # 1 %

# Circuit utilisation thresholds.
LINK_WARN_PCT = 70
LINK_CRIT_PCT = 90

# Voice and other real-time classes are expected never to drop, so they are
# judged harder than bulk traffic.
REALTIME_CLASSES = {"voice", "voip", "real-time", "llq"}


def _ratio(part: int, whole: int) -> float:
    return 0.0 if whole <= 0 else part / whole


def _pct(part: float, whole: float) -> float:
    return 0.0 if whole <= 0 else round(part / whole * 100, 1)


# ------------------------------------------------------------------------ QoS
def analyse_qos(rows: list[dict]) -> dict:
    """Per-queue drop ratios, plus a per-interface roll-up."""
    queues = []
    for row in rows:
        tx = row.get("tx-packets", 0) or 0
        drops = row.get("drop-packets", 0) or 0
        offered = tx + drops          # what the queue was asked to send
        ratio = _ratio(drops, offered)
        cls = (row.get("class-name") or "").lower()
        realtime = cls in REALTIME_CLASSES

        if drops == 0:
            severity = "ok"
        elif realtime or ratio >= QOS_CRIT_RATIO:
            # Any drop in a real-time queue is worth a look, whatever the ratio.
            severity = "critical" if ratio >= QOS_CRIT_RATIO or realtime else "warning"
        elif ratio >= QOS_WARN_RATIO:
            severity = "warning"
        else:
            severity = "ok"

        queues.append({
            "hostname": row.get("host-name"),
            "interface": row.get("interface"),
            "policy": row.get("policy-name"),
            "queue": row.get("queue-id"),
            "klass": row.get("class-name"),
            "tx_packets": tx,
            "tx_bytes": row.get("tx-bytes", 0) or 0,
            "drops": drops,
            "drop_pct": round(ratio * 100, 3),
            "severity": severity,
            "realtime": realtime,
        })

    interfaces: dict[tuple, dict] = {}
    for q in queues:
        key = (q["hostname"], q["interface"])
        entry = interfaces.setdefault(key, {
            "hostname": q["hostname"], "interface": q["interface"],
            "policy": q["policy"], "tx_packets": 0, "drops": 0, "queues": 0,
            "worst_class": None, "worst_pct": 0.0,
        })
        entry["tx_packets"] += q["tx_packets"]
        entry["drops"] += q["drops"]
        entry["queues"] += 1
        if q["drop_pct"] > entry["worst_pct"]:
            entry["worst_pct"] = q["drop_pct"]
            entry["worst_class"] = q["klass"]

    for entry in interfaces.values():
        entry["drop_pct"] = round(
            _ratio(entry["drops"], entry["tx_packets"] + entry["drops"]) * 100, 3
        )

    queues.sort(key=lambda q: q["drop_pct"], reverse=True)

    return {
        "queues": queues,
        "interfaces": sorted(interfaces.values(), key=lambda i: i["drop_pct"], reverse=True),
        "totals": {
            "tx_packets": sum(q["tx_packets"] for q in queues),
            "drops": sum(q["drops"] for q in queues),
            "queues_dropping": sum(1 for q in queues if q["drops"] > 0),
            "realtime_drops": sum(q["drops"] for q in queues if q["realtime"]),
        },
    }


# ---------------------------------------------------------------------- links
def analyse_links(rows: list[dict]) -> dict:
    """Utilisation against each circuit's configured bandwidth."""
    links = []
    for row in rows:
        up = row.get("bandwidth-upstream", 0) or 0
        down = row.get("bandwidth-downstream", 0) or 0
        tx = row.get("tx-kbps", 0) or 0
        rx = row.get("rx-kbps", 0) or 0
        oper_up = str(row.get("if-oper-status", "")).lower() == "up"

        tx_pct = _pct(tx, up)
        rx_pct = _pct(rx, down)
        worst = max(tx_pct, rx_pct)

        if not oper_up:
            severity = "down"
        elif worst >= LINK_CRIT_PCT:
            severity = "critical"
        elif worst >= LINK_WARN_PCT:
            severity = "warning"
        else:
            severity = "ok"

        links.append({
            "hostname": row.get("host-name"),
            "interface": row.get("interface"),
            "color": row.get("color"),
            "bw_up": up,
            "bw_down": down,
            "tx_kbps": tx,
            "rx_kbps": rx,
            "tx_pct": tx_pct,
            "rx_pct": rx_pct,
            "worst_pct": worst,
            "oper_up": oper_up,
            "severity": severity,
        })

    # Busiest first: that is the order an operator reads this table in.
    links.sort(key=lambda link: (not link["oper_up"], -link["worst_pct"]))

    by_color: dict[str, dict] = {}
    for link in links:
        entry = by_color.setdefault(link["color"] or "—", {
            "color": link["color"] or "—", "count": 0,
            "capacity_kbps": 0, "tx_kbps": 0, "rx_kbps": 0,
        })
        entry["count"] += 1
        entry["capacity_kbps"] += link["bw_up"]
        entry["tx_kbps"] += link["tx_kbps"]
        entry["rx_kbps"] += link["rx_kbps"]

    for entry in by_color.values():
        entry["used_pct"] = _pct(entry["tx_kbps"], entry["capacity_kbps"])

    active = [link for link in links if link["oper_up"]]
    return {
        "links": links,
        "by_color": sorted(by_color.values(), key=lambda c: c["capacity_kbps"], reverse=True),
        "totals": {
            "count": len(links),
            "down": sum(1 for link in links if not link["oper_up"]),
            "saturated": sum(1 for link in active if link["severity"] == "critical"),
            "busy": sum(1 for link in active if link["severity"] == "warning"),
            "capacity_kbps": sum(link["bw_up"] for link in active),
            "tx_kbps": sum(link["tx_kbps"] for link in active),
            "rx_kbps": sum(link["rx_kbps"] for link in active),
        },
    }


# ------------------------------------------------------------------------ AAR
def analyse_aar(stats: list[dict], classes: list[dict], events: list[dict]) -> dict:
    """Judge every tunnel against the SLA class bound to it.

    A tunnel is out of SLA when it breaches any one of latency, loss or jitter,
    so the result names which, rather than only saying it failed.
    """
    budgets = {c["name"]: c for c in classes}

    tunnels = []
    for row in stats:
        sla_name = row.get("sla-class")
        budget = budgets.get(sla_name, {})
        latency = row.get("latency")
        loss = row.get("loss")
        jitter = row.get("jitter")

        breaches = []
        if budget:
            if latency is not None and latency > budget.get("latency", float("inf")):
                breaches.append("latency")
            if loss is not None and loss > budget.get("loss", float("inf")):
                breaches.append("loss")
            if jitter is not None and jitter > budget.get("jitter", float("inf")):
                breaches.append("jitter")

        tunnels.append({
            "hostname": row.get("host-name"),
            "local_color": row.get("local-color"),
            "remote_ip": row.get("remote-system-ip"),
            "remote_color": row.get("remote-color"),
            "sla_class": sla_name,
            "policy": row.get("app-route-policy"),
            "latency": latency,
            "loss": loss,
            "jitter": jitter,
            "budget_latency": budget.get("latency"),
            "budget_loss": budget.get("loss"),
            "budget_jitter": budget.get("jitter"),
            "breaches": breaches,
            "in_sla": not breaches,
            # No class bound means nothing to measure against, not a pass.
            "unmeasured": not budget,
        })

    # Violations first, then by how many budgets they blew.
    tunnels.sort(key=lambda t: (t["in_sla"], -len(t["breaches"])))

    per_class = []
    for definition in classes:
        members = [t for t in tunnels if t["sla_class"] == definition["name"]]
        compliant = sum(1 for t in members if t["in_sla"])
        per_class.append({
            **definition,
            "tunnels": len(members),
            "compliant": compliant,
            "violating": len(members) - compliant,
            "compliance_pct": _pct(compliant, len(members)),
        })

    normalised_events = sorted(
        (
            {
                "time": e.get("entry_time"),
                "hostname": e.get("host-name"),
                "policy": e.get("app-route-policy"),
                "sla_class": e.get("sla-class"),
                "from_color": e.get("from-color"),
                "to_color": e.get("to-color"),
                "reason": e.get("reason"),
            }
            for e in events
        ),
        key=lambda e: e["time"] or 0,
        reverse=True,
    )

    violating = [t for t in tunnels if not t["in_sla"]]
    return {
        "tunnels": tunnels,
        "classes": per_class,
        "events": normalised_events,
        "totals": {
            "tunnels": len(tunnels),
            "in_sla": sum(1 for t in tunnels if t["in_sla"]),
            "violating": len(violating),
            "compliance_pct": _pct(len(tunnels) - len(violating), len(tunnels)),
            "switchovers": len(normalised_events),
            "by_reason": _count_reasons(normalised_events),
        },
    }


# -------------------------------------------------------------- enhanced AAR
# Enhanced application-aware routing sends its probes marked with the DSCP of
# the traffic class they represent, instead of one default value. It matters
# because QoS treats DSCP values differently: without it a SLA class is judged
# by whatever queue the default probe lands in, which may be nothing like the
# queue its own traffic rides in — so the figures look fine while the traffic
# they claim to describe is being dropped.
#
# Release floors, by software train.
ENHANCED_AAR_MIN = {
    17: (17, 9, 1),    # Cisco IOS XE Catalyst SD-WAN
    20: (20, 9, 1),    # Catalyst SD-WAN controllers and vEdge
}

# The newest train with a floor recorded above. Anything on a higher train
# postdates the feature, so it is assumed to carry it rather than reported as
# incapable: this check must not mark a fabric as unable to do something its
# software has shipped with for years, just because this table was written
# before that release existed. Trains between the known ones (18.x, 19.x are
# vEdge software predating the 20.x renumbering) never gained it and still
# read as unsupported.
NEWEST_KNOWN_TRAIN = max(ENHANCED_AAR_MIN)


def parse_version(raw: str | None) -> tuple[int, ...] | None:
    """Turn "17.12.3" into (17, 12, 3). Returns None for anything unparseable."""
    if not raw:
        return None
    parts = []
    for piece in str(raw).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def supports_enhanced_aar(raw: str | None) -> tuple[bool, str | None]:
    """Whether this release can run enhanced AAR, and the floor it is judged by."""
    version = parse_version(raw)
    if version is None:
        return False, None

    major = version[0]
    floor = ENHANCED_AAR_MIN.get(major)

    if floor is None:
        # Newer than every train we have a floor for: the feature predates it.
        if major > NEWEST_KNOWN_TRAIN:
            return True, None
        # 18.x / 19.x and anything older never gained it.
        return False, None

    padded = version + (0,) * (len(floor) - len(version))
    return padded[:len(floor)] >= floor, ".".join(str(n) for n in floor)


def version_status(raw: str | None) -> str:
    """How the verdict on this release was reached, for honest reporting.

    "assumed" matters: the dashboard says the release is capable because it
    postdates the feature, not because anyone checked that release.
    """
    version = parse_version(raw)
    if version is None:
        return "unknown"

    major = version[0]
    if major in ENHANCED_AAR_MIN:
        return "checked" if supports_enhanced_aar(raw)[0] else "too_old"
    return "assumed" if major > NEWEST_KNOWN_TRAIN else "too_old"


def analyse_enhanced_aar(
    devices: list[dict],
    sla_definitions: list[dict],
    probe_classes: list[dict],
) -> dict:
    """Check whether enhanced AAR is actually in effect, not merely available.

    Three things have to line up: the software has to support it, an
    app-probe-class has to exist, and each SLA class has to reference one.
    Any of the three missing leaves some traffic measured by default probes.
    """
    # An SLA class references its app-probe-class by the probe list's listId
    # UUID. Names are indexed too, because some vManage builds return the name
    # in that field and resolving only by UUID would report a correctly
    # configured fabric as broken.
    probes_by_id = {str(p["list_id"]): p for p in probe_classes if p.get("list_id")}
    probes_by_name = {p["name"]: p for p in probe_classes if p.get("name")}

    # --- per SLA class ---
    classes = []
    for definition in sla_definitions:
        reference = definition.get("app_probe_class")
        probe = None
        if reference is not None:
            key = str(reference)
            probe = probes_by_id.get(key) or probes_by_name.get(key)

        classes.append({
            "name": definition.get("name"),
            # Show the operator the probe class's name, never a raw UUID.
            "probe_class": (probe or {}).get("name") or (reference if probe else None),
            "enhanced": bool(probe),
            # A binding pointing at a class that does not exist is worse than
            # no binding: it looks configured and measures nothing special.
            "dangling": reference is not None and probe is None,
            "dscp": (probe or {}).get("dscp"),
            "dscp_map": (probe or {}).get("dscp_map") or [],
            "mixed_dscp": bool((probe or {}).get("mixed_dscp")),
            "forwarding_class": (probe or {}).get("forwarding_class"),
            "latency": definition.get("latency"),
            "loss": definition.get("loss"),
            "jitter": definition.get("jitter"),
        })

    # --- per device ---
    edges = [d for d in devices if d.get("device-type") == "vedge"]
    device_rows = []
    for d in edges:
        ok, floor = supports_enhanced_aar(d.get("version"))
        device_rows.append({
            "hostname": d.get("host-name"),
            "system_ip": d.get("system-ip"),
            "site_id": d.get("site-id"),
            "version": d.get("version"),
            "supported": ok,
            # "assumed" when the release is newer than anything in the table:
            # capable by date rather than by verification.
            "basis": version_status(d.get("version")),
            "required": floor,
            "reachable": d.get("reachability") == "reachable",
        })
    # Blockers first: those are the rows an operator has to act on.
    device_rows.sort(key=lambda r: (r["supported"], r["hostname"] or ""))

    enhanced = [c for c in classes if c["enhanced"]]
    blocking = [d for d in device_rows if not d["supported"]]
    bound = {c["probe_class"] for c in classes if c["enhanced"]}
    unused = [p["name"] for p in probe_classes
              if p.get("name") and p["name"] not in bound]

    if not probe_classes:
        state = "disabled"
    elif not enhanced:
        state = "disabled"
    elif len(enhanced) < len(classes) or blocking:
        state = "partial"
    else:
        state = "enabled"

    findings = _enhanced_aar_findings(classes, blocking, unused, probe_classes)

    return {
        "state": state,
        "classes": classes,
        "devices": device_rows,
        "probe_classes": [
            {
                "name": p.get("name"),
                "dscp": p.get("dscp"),
                "forwarding_class": p.get("forwarding_class"),
                "dscp_map": p.get("dscp_map") or [],
                "used_by": [c["name"] for c in classes if c["probe_class"] == p.get("name")],
            }
            for p in probe_classes
        ],
        "findings": findings,
        "totals": {
            "sla_total": len(classes),
            "sla_enhanced": len(enhanced),
            "sla_default": len(classes) - len(enhanced),
            "coverage_pct": _pct(len(enhanced), len(classes)),
            "probe_classes": len(probe_classes),
            "devices_total": len(device_rows),
            "devices_supported": sum(1 for d in device_rows if d["supported"]),
            "devices_blocking": len(blocking),
        },
    }


def _enhanced_aar_findings(classes, blocking, unused, probes) -> list[dict]:
    """What to actually do about it, most severe first."""
    findings = []

    if not probes:
        findings.append({
            "severity": "Major", "key": "eaar.finding.no_probe_classes", "params": {},
        })

    for c in classes:
        if c["dangling"]:
            findings.append({
                "severity": "Critical",
                "key": "eaar.finding.dangling_probe",
                "params": {"sla": c["name"], "probe": c["probe_class"]},
            })
        elif not c["enhanced"]:
            findings.append({
                "severity": "Major",
                "key": "eaar.finding.default_probing",
                "params": {"sla": c["name"]},
            })

    for d in blocking:
        findings.append({
            "severity": "Major",
            "key": "eaar.finding.version_too_old",
            "params": {
                "host": d["hostname"],
                "version": d["version"] or "—",
                "required": d["required"] or "17.9.1",
            },
        })

    for name in unused:
        findings.append({
            "severity": "Minor",
            "key": "eaar.finding.probe_unused",
            "params": {"probe": name},
        })

    order = {"Critical": 0, "Major": 1, "Minor": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return findings


def _count_reasons(events: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        reason = event.get("reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))
